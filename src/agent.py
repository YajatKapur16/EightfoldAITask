import os
import json
import operator
import re
from datetime import datetime
from typing import TypedDict, List, Annotated, Dict, Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, END, START
from langchain_mcp_adapters.client import MultiServerMCPClient
from prompts import MANAGER_PROMPT, PLANNER_PROMPT, WRITER_PROMPT, CLARIFICATION_PROMPT, SUPERVISOR_PROMPT

# --- Input Validation & Security ---
def validate_and_sanitize_input(user_input: str) -> tuple[bool, str, str]:
    """
    Validates user input and detects potential prompt injection attempts.
    Returns: (is_valid, sanitized_input, rejection_reason)
    """
    if not user_input or not user_input.strip():
        return False, "", "Empty input"
    
    # Remove excessive whitespace
    sanitized = ' '.join(user_input.strip().split())
    
    # Check for excessive length (prevent DOS)
    if len(sanitized) > 1000:
        return False, "", "Input too long (max 1000 characters)"
    
    # Detect prompt injection patterns
    injection_patterns = [
        r'ignore (previous|all|above|prior) (instructions|prompts|rules|commands)',
        r'disregard (previous|all|above|prior) (instructions|prompts|rules)',
        r'forget (everything|all|previous|above)',
        r'you are (now|a) (?!a helpful|an? expert)',  # Allow legitimate phrases
        r'new (instructions|prompt|system|role):',
        r'system:?\s*(prompt|message|override)',
        r'\[SYSTEM\]',
        r'\[INST\]',
        r'<\|?im_start\|?>',
        r'<\|?im_end\|?>',
        r'override (previous|system|all)',
        r'pretend (you are|to be|that)',
        r'roleplay as',
        r'simulate (being|that you)',
    ]
    
    lower_input = sanitized.lower()
    for pattern in injection_patterns:
        if re.search(pattern, lower_input, re.IGNORECASE):
            return False, "", "Potential prompt injection detected"
    
    # Check for excessive special characters (potential obfuscation)
    special_char_ratio = sum(not c.isalnum() and not c.isspace() for c in sanitized) / len(sanitized)
    if special_char_ratio > 0.3:
        return False, "", "Suspicious character patterns detected"
    
    # Check if input is relevant to business research
    irrelevant_patterns = [
        r'^(hello|hi|hey|yo)$',  # Pure greetings are ok
        r'(recipe|weather|movie|game|joke|story)',
        r'solve (this|my) (math|equation|problem)',
        r'translate (this|to|from)',
        r'write (a|me) (poem|song|letter)',
    ]
    
    # Mark as potentially irrelevant but don't reject (let manager handle)
    for pattern in irrelevant_patterns:
        if re.search(pattern, lower_input):
            # This will be handled by manager_node to give appropriate response
            pass
    
    return True, sanitized, ""

# --- 1. Setup MCP Tools ---
# Removed get_mcp_tools as it is now handled inside researcher_node

# --- 2. State Definition ---
class AgentState(TypedDict):
    # Conversation History
    messages: Annotated[List[BaseMessage], operator.add]
    # Context
    company_input: str
    user_persona: str  # CHATTY, CONFUSED, TASK, UPDATE, EFFICIENT
    user_provided_urls: List[str] # New field for user URLs
    clarification_needed: bool # Flag for clarification
    conversation_summary: str
    # The Plan
    research_plan: List[str]
    current_step_index: int
    # The Data
    research_data: Annotated[List[str], operator.add]
    sources_used: Annotated[List[str], operator.add]  # Track URLs and sources
    thought_trace: Annotated[List[Dict], operator.add] # Track detailed thought process
    final_report: str


def extract_last_report(messages: List[Any]) -> str:
    """Return the most recent assistant report-style message for context reuse."""
    for msg in reversed(messages):
        role = ""
        content = ""
        if isinstance(msg, BaseMessage):
            role = getattr(msg, "type", "")
            content = getattr(msg, "content", "")
        elif isinstance(msg, dict):
            role = msg.get("role") or msg.get("type", "")
            content = msg.get("content", "")

        if role == "ai" and content:
            if len(content) > 400 or "Executive Summary" in content or "Company Overview" in content:
                return content
    return ""


async def summarize_conversation_if_needed(llm: ChatGoogleGenerativeAI, state: AgentState) -> Dict[str, str]:
    """Maintain a running summary once the conversation grows large."""
    messages = state.get("messages", [])
    if len(messages) <= 10:
        return {}

    existing_summary = state.get("conversation_summary", "")
    if existing_summary:
        history_scope = messages[-9:-1]  # summarize the latest exchanges only
    else:
        history_scope = messages[:-1]

    formatted = []
    for msg in history_scope:
        if isinstance(msg, BaseMessage):
            role = getattr(msg, "type", "unknown").upper()
            content = getattr(msg, "content", "")
        elif isinstance(msg, dict):
            role = (msg.get("role") or msg.get("type", "unknown")).upper()
            content = msg.get("content", "")
        else:
            continue

        if not content:
            continue
        formatted.append(f"{role}: {content}")

    if not formatted:
        return {}

    history_text = "\n".join(formatted)[-4000:]
    summary_response = await llm.ainvoke([
        SystemMessage(content="You maintain a running summary of a business research chat. You MUST explicitly state the 'Current Focus Company' at the start. Keep key objectives, constraints, and unresolved questions in under 220 words."),
        HumanMessage(content=f"Existing Summary:\n{existing_summary or 'None'}\n\nNew Conversation Segment:\n{history_text}")
    ])

    updated_summary = summary_response.content.strip()
    if not updated_summary:
        return {}
    return {"conversation_summary": updated_summary}

# --- 3. Nodes ---

async def manager_node(state: AgentState):
    """Classifies intent and routes the conversation with security validation"""
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0, max_output_tokens=65535)
    
    # Analyze last message
    last_msg = state["messages"][-1].content
    
    # Validate and sanitize input
    is_valid, sanitized_input, rejection_reason = validate_and_sanitize_input(last_msg)
    
    if not is_valid:
        # Mark as IRRELEVANT to be handled by rejection handler
        return {
            "user_persona": "IRRELEVANT",
            "final_report": f"I cannot process this request. {rejection_reason}. Please provide a valid company research request."
        }
    
    # Update the message with sanitized input
    state["messages"][-1].content = sanitized_input

    summary_update = await summarize_conversation_if_needed(llm, state)

    # Prepare context for Manager
    recent_history = state["messages"][-5:-1] # Get last few messages excluding current
    history_str = "\n".join([f"{msg.type.upper()}: {msg.content}" for msg in recent_history])
    conversation_summary = state.get("conversation_summary", "")
    
    context_input = f"""
    Conversation Summary: {conversation_summary}
    Recent History:
    {history_str}
    
    Current User Input: {sanitized_input}
    """

    response = await llm.ainvoke([
        SystemMessage(content=MANAGER_PROMPT),
        HumanMessage(content=context_input)
    ])
    
    try:
        # Clean JSON formatting
        clean_json = response.content.replace("```json", "").replace("```", "").strip()
        decision = json.loads(clean_json)
        persona = decision.get("persona", "TASK")
        
        # Additional validation: ensure persona is valid
        valid_personas = ["CHATTY", "CONFUSED", "TASK", "UPDATE", "IRRELEVANT", "EFFICIENT", "EDIT"]
        if persona not in valid_personas:
            persona = "TASK"  # Default to TASK if invalid

        result = {"user_persona": persona}

        # Default to current input
        result["company_input"] = sanitized_input

        # Use refined_query as company_input if available and valid
        if decision.get("refined_query") and len(decision["refined_query"]) > len(sanitized_input):
             result["company_input"] = decision["refined_query"]
        elif decision.get("detected_entity"):
             # Fallback: if refined query wasn't generated but entity was detected
             result["company_input"] = f"{decision['detected_entity']}: {sanitized_input}"

        result.update(summary_update)
        return result
    except Exception as e:
        # Fallback - try to infer from content
        lower_input = sanitized_input.lower()
        if any(greeting in lower_input for greeting in ["hello", "hi", "hey"]):
            persona = "CHATTY"
        elif any(word in lower_input for word in ["help", "what", "how"]):
            persona = "CONFUSED"
        elif any(word in lower_input for word in ["quick", "fast", "summary"]):
            persona = "EFFICIENT"
        elif any(word in lower_input for word in ["rewrite", "format", "table", "edit", "change"]):
            persona = "EDIT"
        else:
            persona = "TASK"

        result = {"user_persona": persona, "company_input": sanitized_input}
        result.update(summary_update)
        return result

async def planner_node(state: AgentState):
    """Generates the research plan with validation"""
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0, max_output_tokens=65535)
    
    company_input = state.get("company_input", "")
    persona = state.get("user_persona", "TASK")
    
    # Context handling
    context_sections = []
    conversation_summary = state.get("conversation_summary", "")
    if conversation_summary:
        context_sections.append(f"CONVERSATION SUMMARY:\n{conversation_summary}")

    include_previous_report = persona in ["UPDATE", "EDIT"]
    if not include_previous_report and persona in ["TASK", "EFFICIENT"]:
        lower_input = company_input.lower()
        if any(ref in lower_input for ref in ["the company", "this company", "it ", "its ", "their "]):
            include_previous_report = True

    previous_report = ""
    if include_previous_report and len(state.get("messages", [])) > 1:
        previous_report = extract_last_report(state["messages"][:-1])
        if previous_report:
            context_sections.append(f"PREVIOUS REPORT CONTEXT:\n{previous_report[:2000]}...")

    context_str = "\n\n".join(context_sections)

    if not company_input or not company_input.strip():
        return {
            "research_plan": ["Unable to create plan without valid company input"],
            "current_step_index": 0,
            "research_data": [],
            "thought_trace": [],
            "final_report": "Please provide a specific company or research topic."
        }
    
    try:
        augmented_input = company_input
        if context_str:
            augmented_input = f"{company_input}\n\n{context_str}" if company_input else context_str

        response = await llm.ainvoke(
            PLANNER_PROMPT.format(
                company_input=augmented_input,
                persona=persona
            )
        )
        
        clean_json = response.content.replace("```json", "").replace("```", "").strip()
        plan = json.loads(clean_json)
        
        # Validate plan structure
        if not isinstance(plan, list) or len(plan) == 0:
            raise ValueError("Invalid plan structure")
        
        # Ensure plan is not too long (prevent resource exhaustion)
        if len(plan) > 10:
            plan = plan[:10]  # Limit to 10 steps
        
        # Sanitize each step
        sanitized_plan = []
        for step in plan:
            if isinstance(step, str) and step.strip():
                sanitized_plan.append(step.strip()[:500])  # Limit step length
        
        if not sanitized_plan:
            raise ValueError("No valid steps in plan")
        
        # Check for AMBIGUOUS_REQUEST flag from Planner
        if len(sanitized_plan) == 1 and sanitized_plan[0].startswith("AMBIGUOUS_REQUEST:"):
            return {
                "research_plan": sanitized_plan,
                "current_step_index": 0,
                "research_data": [],
                "thought_trace": [],
                "clarification_needed": True, # Flag to stop execution
                "final_report": sanitized_plan[0] # Pass the question to the next node
            }

        return {
            "research_plan": sanitized_plan,
            "current_step_index": 0,
            "research_data": [],
            "thought_trace": []
        }
    except Exception as e:
        # Fallback to basic plan
        return {
            "research_plan": [f"Research company overview for {company_input}", f"Analyze market position of {company_input}"],
            "current_step_index": 0,
            "research_data": [],
            "thought_trace": [{"step": "planning", "type": "error", "content": f"Plan generation failed: {str(e)}, using fallback plan"}]
        }

# --- Redefined Nodes for Enhanced Logic ---

async def researcher_node(state: AgentState):
    """Executes the current step using Tools with a tiered strategy and dynamic fallback"""
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0, max_output_tokens=65535)
    persona = state.get("user_persona", "TASK")
    
    # Initialize client with all 3 servers
    client = MultiServerMCPClient(
        {
            "tavily": {
                "url": os.getenv("MCP_TAVILY_URL"),
                "transport": "sse",
            },
            "ddg": {
                "url": os.getenv("MCP_DDG_URL"),
                "transport": "sse",
            },
            "scraper": {
                "url": os.getenv("MCP_SCRAPER_URL"),
                "transport": "sse",
            }
        }
    )
    
    current_task = state["research_plan"][state["current_step_index"]]
    user_urls = state.get("user_provided_urls", [])

    # Get tools directly (no context manager)
    try:
        tools = await client.get_tools()
    except Exception as e:
        return {
            "research_data": [f"Step: {current_task}\nError: Failed to connect to MCP tools. Please ensure the services are running. Details: {str(e)}"],
            "current_step_index": state["current_step_index"] + 1, # Increment to avoid infinite loop
            "thought_trace": [{"step": current_task, "type": "error", "content": str(e)}]
        }
    
    # Create a map for easy tool execution
    tool_map = {tool.name: tool for tool in tools}
    llm_with_tools = llm.bind_tools(tools)
    
    # Agentic Loop Prompt - Adaptive based on Persona
    if persona == "EFFICIENT":
        sys_msg_content = """You are an Efficient Research Assistant.
        Your goal is SPEED and ACCURACY.
        
        MANDATORY WORKFLOW:
        1. **SEARCH**: Use 'research_query' (Tavily) FIRST. This is your primary tool for quick, comprehensive context.
        2. **EVALUATE**: Check if the 'research_query' results provide the EXACT answer requested.
        3. **ENRICH**: IF retrieved data is insufficient or missing specific details, use 'scrape_dynamic_webpage' on the most relevant URLs found.
        4. **FALLBACK**: Use 'ddg_search' only if 'research_query' fails completely.
        
        DO NOT skip 'research_query'. DO NOT scrape unless necessary for enrichment.
        """
    else:
        sys_msg_content = """You are an expert research assistant. 
        You MUST follow a strict workflow to ensure high-quality, deep research.
        
        MANDATORY WORKFLOW:
        1. **SEARCH**: Use 'ddg_search' to find relevant URLs.
        2. **SELECT**: Identify the top 3-5 most promising URLs from the search results.
        3. **SCRAPE**: Use 'scrape_dynamic_webpage' (Playwright) on ALL selected URLs to extract detailed content. 
           - DO NOT scrape just one. Scrape MULTIPLE sources to ensure coverage.
           - This is MANDATORY.
        4. **ANALYZE**: Use the scraped content to answer the task.
        5. **FALLBACK**: ONLY if the above fails to yield sufficient info, use 'research_query' (Tavily).
        
        Do not stop after just searching. You must scrape multiple sources to get deep insights.
        """
    
    context_str = ""
    if user_urls:
        context_str = f"The user has provided specific URLs to check: {user_urls}. Start by scraping these."

    initial_human_msg = HumanMessage(content=f"""
    Execute this Research Task: "{current_task}".
    
    {context_str}
    
    Goal: Gather detailed, comprehensive information to satisfy the task "{current_task}".
    """)
    
    messages = [SystemMessage(content=sys_msg_content), initial_human_msg]

    # Helper to run the agent loop
    async def run_agent_loop(msgs, max_turns=5):
        step_thoughts = []
        step_urls = []
        scraped_urls = set()
        scrape_used = False
        scrape_warning = ""
        
        for _ in range(max_turns):
            response = await llm_with_tools.ainvoke(msgs)
            msgs.append(response)
            
            if not response.tool_calls:
                break
                
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                
                if tool_name in tool_map:
                    try:
                        tool_result = await tool_map[tool_name].ainvoke(tool_args)
                    except Exception as e:
                        tool_result = f"Error executing {tool_name}: {str(e)}"
                    
                    result_str = str(tool_result)
                    found_urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', result_str)
                    step_urls.extend(found_urls)
                    
                    if "url" in tool_args:
                        step_urls.append(tool_args["url"])
                    if "urls" in tool_args and isinstance(tool_args["urls"], list):
                        step_urls.extend(tool_args["urls"])
                    
                    step_thoughts.append({
                        "step": current_task,
                        "type": "tool_execution",
                        "tool": tool_name,
                        "input": tool_args,
                        "output": result_str[:1000] + "..." if len(result_str) > 1000 else result_str
                    })

                    msgs.append(HumanMessage(
                        content=f"Tool '{tool_name}' Output: {tool_result}",
                        name=tool_name
                    ))

                    if tool_name == "scrape_dynamic_webpage":
                        scrape_used = True
                        if "url" in tool_args:
                            scraped_urls.add(tool_args["url"])
        
        return step_thoughts, step_urls, scraped_urls, scrape_used, scrape_warning

    # --- EXECUTION PHASE 1 ---
    step_thoughts, step_urls, scraped_urls, scrape_used, scrape_warning = await run_agent_loop(messages)

    # Enforce scraping (Phase 1)
    if persona != "EFFICIENT":
        scraper_tool = tool_map.get("scrape_dynamic_webpage")
        candidates = []
        if user_urls: candidates.extend(user_urls)
        candidates.extend(step_urls)
        
        unique_candidates = []
        seen = set()
        for url in candidates:
            if url not in seen and url not in scraped_urls:
                unique_candidates.append(url)
                seen.add(url)
        
        max_scrape_count = 3
        count = 0
        if scraper_tool and unique_candidates:
            for url in unique_candidates:
                if count >= max_scrape_count: break
                try:
                    tool_result = await scraper_tool.ainvoke({"url": url})
                    step_thoughts.append({
                        "step": current_task,
                        "type": "tool_execution",
                        "tool": "scrape_dynamic_webpage",
                        "input": {"url": url},
                        "output": str(tool_result)[:1000] + "..."
                    })
                    messages.append(HumanMessage(content=f"Tool 'scrape_dynamic_webpage' Output: {tool_result}", name="scrape_dynamic_webpage"))
                    scraped_urls.add(url)
                    count += 1
                    scrape_used = True
                except Exception as exc:
                    step_thoughts.append({"step": current_task, "type": "error", "content": f"Scrape failed: {exc}"})

    # Finalize Phase 1
    async def finalize_model_response(msgs) -> str:
        if not msgs: return ""
        last_msg = msgs[-1]
        if isinstance(last_msg, HumanMessage) and getattr(last_msg, "name", None):
            final_ai_msg = await llm_with_tools.ainvoke(msgs)
            msgs.append(final_ai_msg)
            return final_ai_msg.content
        return last_msg.content

    content = await finalize_model_response(messages)
    if isinstance(content, list): content = " ".join([str(c) for c in content])
    elif not isinstance(content, str): content = str(content)

    # --- SUPERVISOR CHECK (Phase 1) ---
    supervisor_response = await llm.ainvoke(SUPERVISOR_PROMPT.format(current_task=current_task, content=content[:10000]))
    try:
        clean_json = supervisor_response.content.replace("```json", "").replace("```", "").strip()
        decision = json.loads(clean_json)
        status = decision.get("status", "CLEAR")
        user_question = decision.get("user_question", "")
    except:
        status = "CLEAR"
        user_question = ""

    # --- DYNAMIC FALLBACK LOGIC ---
    # If status is INSUFFICIENT_DATA or content is too short, AND we haven't tried Tavily yet
    is_insufficient = (status == "INSUFFICIENT_DATA") or (len(content.strip()) < 100)
    
    # Check if Tavily was used in Phase 1
    tavily_used = any(t.get("tool") == "research_query" for t in step_thoughts)

    if is_insufficient and not tavily_used and persona != "EFFICIENT":
        # TRIGGER PHASE 2: FALLBACK
        fallback_instruction = f"""
        [SYSTEM NOTICE]: The previous research attempt yielded INSUFFICIENT DATA.
        
        You MUST now switch strategy:
        1. Use 'research_query' (Tavily) immediately. This is a more powerful search tool.
        2. Search for: "{current_task}"
        3. Scrape the top results from Tavily.
        
        DO NOT give up. You must find the information.
        """
        messages.append(HumanMessage(content=fallback_instruction))
        
        # Run Loop Again (Phase 2)
        p2_thoughts, p2_urls, p2_scraped, p2_used, p2_warn = await run_agent_loop(messages, max_turns=3)
        
        # Merge results
        step_thoughts.extend(p2_thoughts)
        step_urls.extend(p2_urls)
        scraped_urls.update(p2_scraped)
        
        # Finalize Phase 2
        content = await finalize_model_response(messages)
        if isinstance(content, list): content = " ".join([str(c) for c in content])
        elif not isinstance(content, str): content = str(content)
        
        # Re-evaluate with Supervisor
        supervisor_response = await llm.ainvoke(SUPERVISOR_PROMPT.format(current_task=current_task, content=content[:10000]))
        try:
            clean_json = supervisor_response.content.replace("```json", "").replace("```", "").strip()
            decision = json.loads(clean_json)
            status = decision.get("status", "CLEAR")
            user_question = decision.get("user_question", "")
        except:
            status = "CLEAR"
            user_question = ""

    # --- FINAL RETURN ---
    unique_urls = list(set(step_urls))
    
    if status == "AMBIGUOUS" and state["current_step_index"] == 0:
         from langchain_core.messages import AIMessage
         return {
            "messages": [AIMessage(content=f"⚠️ **Clarification Needed:** {user_question}")],
            "clarification_needed": True,
            "research_data": [f"Step: {current_task}\nStatus: AMBIGUOUS"],
            "sources_used": unique_urls,
            "thought_trace": step_thoughts + [{"step": current_task, "type": "supervisor", "status": status, "content": user_question}]
        }
    
    final_data_entry = f"Step: {current_task}\nFinding: {content}"
    if status != "CLEAR":
        final_data_entry += f"\n\n[WARNING: {status}] {user_question}"

    return {
        "research_data": [final_data_entry],
        "sources_used": unique_urls,
        "current_step_index": state["current_step_index"] + 1,
        "thought_trace": step_thoughts
    }

async def writer_node(state: AgentState):
    """Synthesizes the final report"""
    # Increased max_output_tokens to prevent cutoff
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0.4, max_output_tokens=65535)
    
    # Handle Research Data
    research_data = state.get("research_data", [])
    if not research_data:
        data_str = "No new research data gathered. Rely on Previous Report and User Instructions."
    else:
        data_str = "\n\n".join(research_data)
    
    # Escape curly braces to prevent format string errors
    data_str = data_str.replace("{", "{{").replace("}", "}}")
    current_date = datetime.now().strftime("%B %d, %Y")
    
    # Get previous report from history + summaries
    message_history = state.get("messages", [])
    previous_report = extract_last_report(message_history[:-1]) if message_history else ""
    if not previous_report:
        previous_report = state.get("final_report", "No previous report found in conversation history.")

    conversation_summary = state.get("conversation_summary", "")
    if conversation_summary:
        previous_report = f"Conversation Summary:\n{conversation_summary}\n\n{previous_report}"
    
    # Escape curly braces in previous report too
    previous_report = previous_report.replace("{", "{{").replace("}", "}}")

    response = await llm.ainvoke(
        WRITER_PROMPT.format(
            company_input=state["company_input"],
            persona=state["user_persona"],
            date=current_date,
            research_data=data_str,
            previous_report=previous_report
        )
    )

    final_output = response.content.strip()

    warning_lines = []
    for entry in research_data:
        for line in entry.splitlines():
            if "[WARNING:" in line:
                cleaned = line.strip()
                if cleaned and cleaned not in warning_lines:
                    warning_lines.append(cleaned)

    if warning_lines and "Outstanding Questions & Critical Uncertainties" not in final_output:
        final_output += "\n\n### ❓ Outstanding Questions & Critical Uncertainties\n"
        for note in warning_lines:
            cleaned_note = re.sub(r"\[WARNING:\s*([^\]]+)\]\s*", lambda match: f"{match.group(1).strip()}: ", note)
            final_output += f"- {cleaned_note or note}\n"

    # # Append sources
    # sources = state.get("sources_used", [])
    # if sources:
    #     unique_sources = list(set(sources))
    #     final_output += "\n\n### 📚 Sources\n"
    #     for i, source in enumerate(unique_sources, 1):
    #         final_output += f"{i}. {source}\n"
    return {"final_report": final_output}

async def chat_node(state: AgentState):
    """Handles small talk, confusion, or irrelevant inputs"""
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0.7, max_output_tokens=65535)
    persona = state.get("user_persona", "CHATTY")
    
    # If marked as irrelevant, return rejection message immediately
    if persona == "IRRELEVANT":
        rejection_msg = state.get("final_report", "I can only help with company research and business analysis. Please provide a relevant research request.")
        # Create a proper message response
        from langchain_core.messages import AIMessage
        return {"messages": [AIMessage(content=rejection_msg)]}
    
    prompt = """You are a helpful business research assistant specialized in company analysis and account planning.
    
IMPORTANT: You can ONLY help with:
- Company research and analysis
- Competitive intelligence
- Market positioning
- Business strategy insights
- Account planning

You CANNOT help with:
- General knowledge questions
- Personal advice
- Entertainment (jokes, stories, games)
- Math problems unrelated to business
- Creative writing
- Translation services

If the user asks about something outside your scope, politely decline and redirect them to your core capabilities."""
    
    if persona == "CHATTY":
        prompt += "\n\nThe user is being chatty. Be polite, brief, and gently steer them towards researching a company. If they're asking about something unrelated to business research, politely decline and explain your capabilities."
    
    try:
        response = await llm.ainvoke([SystemMessage(content=prompt)] + state["messages"])
        return {"messages": [response]}
    except Exception as e:
        from langchain_core.messages import AIMessage
        error_response = AIMessage(content="I'm here to help with company research and business analysis. What company would you like me to research?")
        return {"messages": [error_response]}

async def clarifier_node(state: AgentState):
    """Asks clarifying questions to enrich user intent"""
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", temperature=0.7, max_output_tokens=65535)
    
    # Check if we have a specific ambiguity message from Planner
    if state.get("final_report", "").startswith("AMBIGUOUS_REQUEST:"):
        question = state["final_report"].replace("AMBIGUOUS_REQUEST:", "").strip()
        from langchain_core.messages import AIMessage
        return {"messages": [AIMessage(content=f"⚠️ **Clarification Needed:** {question}")], "clarification_needed": True}

    response = await llm.ainvoke(
        CLARIFICATION_PROMPT.format(
            user_input=state["messages"][-1].content,
            persona=state.get("user_persona", "CONFUSED")
        )
    )
    
    return {"messages": [response], "clarification_needed": True}

# --- 4. Graph Construction ---
workflow = StateGraph(AgentState)

workflow.add_node("manager", manager_node)
workflow.add_node("planner", planner_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("writer", writer_node)
workflow.add_node("chat_handler", chat_node)
workflow.add_node("clarifier", clarifier_node)

workflow.add_edge(START, "manager")

def route_manager(state):
    persona = state.get("user_persona", "TASK")
    
    # Handle irrelevant inputs
    if persona == "IRRELEVANT":
        return "chat_handler"
    
    if persona == "TASK" or persona == "UPDATE" or persona == "EFFICIENT":
        return "planner"
    elif persona == "EDIT":
        return "writer"
    elif persona == "CONFUSED":
        return "clarifier"
    else:
        return "chat_handler"

workflow.add_conditional_edges("manager", route_manager)
workflow.add_edge("chat_handler", END)
workflow.add_edge("clarifier", END)

# workflow.add_edge("planner", "researcher")

def route_researcher(state):
    # Check if clarification is needed (from Planner or Supervisor)
    if state.get("clarification_needed"):
        # If it came from Planner (AMBIGUOUS_REQUEST), we might want to route to clarifier or just END
        # For now, END is fine as the UI will show the message
        return END

    # If we have more steps in the plan, loop back to researcher
    if state["current_step_index"] < len(state["research_plan"]):
        return "researcher"
    return "writer"

workflow.add_conditional_edges("researcher", route_researcher)
workflow.add_edge("writer", END)

# Add conditional edge from planner to handle immediate ambiguity
def route_planner(state):
    if state.get("clarification_needed"):
        return "clarifier"
    return "researcher"

# Remove the direct edge and add conditional
# workflow.add_edge("planner", "researcher") # REMOVE THIS LINE
workflow.add_conditional_edges("planner", route_planner)

app_graph = workflow.compile()

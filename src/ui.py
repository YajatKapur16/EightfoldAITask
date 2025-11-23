import streamlit as st
import asyncio
import os
from agent import app_graph
from langchain_core.messages import HumanMessage, AIMessage
import db

st.set_page_config(page_title="M.C.P. Research Agent", layout="wide", page_icon="🕵️‍♂️")

# Initialize DB
db.init_db()

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "refine_request" not in st.session_state:
    st.session_state.refine_request = None
if "pending_input" not in st.session_state:
    st.session_state.pending_input = None
if "current_session_id" not in st.session_state:
    # Try to reuse the last empty session if available
    last_empty = db.get_last_empty_session()
    if last_empty:
        st.session_state.current_session_id = last_empty
    else:
        st.session_state.current_session_id = db.create_session("New Session")

# CSS for better aesthetics
st.markdown("""
<style>
    .stChatMessage { 
        border-radius: 10px; 
        padding: 10px; 
    }
    .step-box { 
        border-left: 4px solid #4CAF50; 
        padding: 10px; 
        margin: 5px 0; 
        background-color: #f0f2f6; 
        color: #31333F; 
    }
    .thinking-section {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 15px;
        margin: 10px 0;
        border-left: 4px solid #007acc;
    }
    .insight-highlight {
        background-color: #e8f4fd;
        padding: 8px;
        border-radius: 5px;
        margin: 5px 0;
    }
    .source-link {
        font-size: 0.9em;
        color: #666;
    }
</style>
""", unsafe_allow_html=True)

st.title("M.C.P. (Master Corporate Profiler)")
st.caption("Powered by Gemini 2.5 • DuckDuckGo • Tavily • Playwright Scraper • LangGraph")

def delete_session_callback(session_id):
    try:
        db.delete_session(session_id)
        if st.session_state.current_session_id == session_id:
            st.session_state.current_session_id = db.create_session("New Session")
            st.session_state.messages = []
        st.toast("Chat deleted successfully!")
    except Exception as e:
        st.error(f"Error deleting chat: {e}")

# Sidebar for configuration
with st.sidebar:
    st.header("Configuration")
    
    # Session Management
    st.subheader("History")
    sessions = db.get_sessions()
    
    # New Chat Button
    if st.button("New Chat", use_container_width=True):
        new_id = db.create_session("New Session")
        st.session_state.current_session_id = new_id
        st.session_state.messages = []
        st.rerun()
        
    st.markdown("---")
    
    # Session List (ChatGPT-style)
    for session in sessions:
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            # Highlight current session
            is_current = session["id"] == st.session_state.current_session_id
            label = f"**{session['name']}**" if is_current else session['name']
            
            if st.button(label, key=f"btn_{session['id']}", use_container_width=True):
                st.session_state.current_session_id = session['id']
                st.session_state.messages = db.get_messages(session['id'])
                st.rerun()
        with col2:
            st.button("🗑️", key=f"del_{session['id']}", help="Delete Chat", on_click=delete_session_callback, args=(session['id'],))

    st.markdown("---")
    user_urls_input = st.text_area("Target URLs (Optional)", help="Enter specific URLs to scrape, one per line.")
    user_urls = [url.strip() for url in user_urls_input.split('\n') if url.strip()]
    
    st.markdown("---")
    st.header("Report Actions")
    if st.session_state.messages and len(st.session_state.messages) > 1:
        refine_input = st.text_area("Refine Last Report", 
                                  placeholder="e.g., 'Focus more on AI initiatives' or 'Add competitor pricing analysis'",
                                  help="Enter specific refinements you want to the last generated report")
        if st.button("Refine Report", disabled=not refine_input, help="Stream a non-blocking refinement of the last report"):
            st.session_state.refine_request = refine_input
            st.rerun()  # Immediately trigger refinement

# Display History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])



# Input
user_input = st.chat_input("Type a company name (e.g., 'Analyze Snowflake') or say Hi...")

async def process_chat(user_input, user_urls, is_refinement=False):
    # Validate input before processing
    if not user_input or not user_input.strip():
        st.error("⚠️ Please enter a valid input.")
        return
    
    # Basic length check
    if len(user_input) > 1000:
        st.error("⚠️ Input is too long. Please limit your request to 1000 characters.")
        return
    
    # Add user message to state (only if not a refinement)
    if not is_refinement:
        st.session_state.messages.append({"role": "user", "content": user_input})
        db.save_message(st.session_state.current_session_id, "user", user_input)
        
        # Display user message immediately
        with st.chat_message("user"):
            st.markdown(user_input)
        
        # Session name update is now handled before process_chat to ensure UI updates immediately

    # Prepare LangGraph inputs
    # Convert session history to LangChain messages
    history = []
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            history.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            history.append(AIMessage(content=msg["content"]))

    inputs = {
        "messages": history,
        "company_input": user_input,
        "user_provided_urls": user_urls,
        "research_data": [],
        "sources_used": [],
        "current_step_index": 0
    }
    
    final_response = ""
    thinking_steps = []
    sources_used = []
    research_insights = []
    current_research_plan = []
    total_findings_count = 0 # Track total findings
    
    with st.chat_message("assistant"):
        # Chain of Thought Display - Show BEFORE outputs
        if is_refinement:
            st.markdown("### **Refinement Process**")
        else:
            st.markdown("### **Agent Thought Process**")
        thinking_container = st.container()
        
        # Create a summary container for insights
        insights_container = st.container()
        
        # Stream graph updates
        async for output in app_graph.astream(inputs):
            for key, value in output.items():
                
                with thinking_container:
                    if key == "manager":
                        persona = value.get("user_persona", "Unknown")
                        with st.expander("Intent Analysis & Routing", expanded=True):
                            st.write(f"**Analyzing Input:** `{user_input}`")
                            st.write(f"**Detected Pattern:** `{persona}`")
                            st.write(f"**User Persona Classification:**")
                            if persona == "TASK":
                                st.success("✅ **Research Task Detected** - User wants comprehensive company analysis")
                                st.write("**Next Action:** Routing to Research Planning Module")
                            elif persona == "EFFICIENT":
                                st.success("**Efficient Task Detected** - User wants quick, targeted results")
                                st.write("**Next Action:** Routing to Accelerated Research Module")
                            elif persona == "UPDATE":
                                st.info("**Update Request Detected** - User wants specific information updates")
                                st.write("**Next Action:** Routing to Targeted Research Module")
                            else:
                                st.info("**Conversational Input** - User needs assistance or clarification")
                                st.write("**Next Action:** Routing to Chat Handler")
                        thinking_steps.append(f"Intent Analysis: {persona}")
                    
                    elif key == "planner":
                        plan = value.get("research_plan", [])
                        current_research_plan = plan
                        with st.expander("Research Strategy & Planning", expanded=True):
                            st.write(f"**Target Company:** `{user_input}`")
                            st.write(f"**Reasoning:** Analyzing input context and user intent to create optimal research strategy")
                            st.write(f"**Custom Research Plan Generated:**")
                            st.write("*Plan dynamically adapts based on input specificity and user requirements*")
                            for i, step in enumerate(plan):
                                st.write(f"   **Step {i+1}:** {step}")
                            st.write(f"**Research Tools Pipeline:** DuckDuckGo (discovery) → Web Scraper (deep-dive) → Tavily (fallback)")
                            st.write(f"**Strategy Rationale:** Tiered approach optimizes cost-effectiveness and thoroughness")
                            st.success(f"✅ **Strategic Plan Created** - {len(plan)} targeted research steps")
                        thinking_steps.append(f"Research Planning: {len(plan)} steps created")
                        research_insights.append(f"Generated {len(plan)}-step research strategy tailored to '{user_input}'")
                                
                    elif key == "researcher":
                        step_idx = value.get("current_step_index", 0)
                        
                        # Use captured plan
                        if current_research_plan and step_idx > 0 and (step_idx - 1) < len(current_research_plan):
                            current_plan_step = current_research_plan[step_idx-1]
                        else:
                            current_plan_step = "Research Step"
                            
                        data = value.get("research_data", [])[-1] if value.get("research_data") else "Researching..."
                        
                        # Extract key finding from data
                        step_lines = data.split('\n')
                        finding_line = ""
                        for line in step_lines:
                            if line.startswith("Finding:"):
                                finding_line = line[8:].strip()
                                break
                        if not finding_line and len(step_lines) > 1:
                            finding_line = step_lines[1]
                        
                        # Count sources accessed in this step
                        step_sources = value.get("sources_used", [])
                        if step_sources:
                            sources_used.extend(step_sources)
                        
                        # Extract URLs from content for additional sources
                        import re
                        if "http" in data:
                            urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', data)
                            sources_used.extend(urls)
                        
                        # Get thought trace
                        thoughts = value.get("thought_trace", [])
                        
                        # Check for conflict messages
                        if "messages" in value:
                            msg_content = value["messages"][0].content
                            final_response = msg_content
                            # Only show warning if it's not already formatted as one
                            if not msg_content.startswith("⚠️"):
                                st.warning(f"⚠️ **Conflict Detected:** {msg_content}")
                            else:
                                st.markdown(msg_content)
                            thinking_steps.append("Research Paused: Conflict Detected")

                        with st.expander(f"Research Execution - Step {step_idx}", expanded=True):
                            st.write(f"**Current Task:** {current_plan_step}")
                            st.write(f"**Execution Reasoning:** Following tiered research strategy to optimize information quality vs cost")
                            st.write(f"**Tools Pipeline:** DuckDuckGo (discovery) → Web Scraper (deep-dive) → Tavily (fallback)")
                            
                            # Display Detailed Thought Trace
                            if thoughts:
                                st.markdown("#### Detailed Thought Trace")
                                for thought in thoughts:
                                    if thought.get("type") == "tool_execution":
                                        st.markdown(f"**Tool Call:** `{thought.get('tool')}`")
                                        with st.expander(f"Input: {str(thought.get('input'))[:50]}...", expanded=False):
                                            st.code(thought.get('input'))
                                        with st.expander(f"Output: {str(thought.get('output'))[:50]}...", expanded=False):
                                            st.code(thought.get('output'))
                                    elif thought.get("type") == "error":
                                        st.error(f"**Error:** {thought.get('content')}")

                            st.write(f"**Data Collection Progress:**")
                            if finding_line:
                                st.write(f"   • **Key Discovery:** {finding_line[:250]}{'...' if len(finding_line) > 250 else ''}")
                            
                            current_sources = len(step_sources) + len(re.findall(r'http', data))
                            if current_sources > 0:
                                st.info(f"**Sources Processed:** {current_sources} URL(s) analyzed in this research step")
                                st.write(f"**Information Quality:** Extracting relevant business intelligence and competitive insights")
                                research_insights.append(f"Step {step_idx}: Processed {current_sources} sources - {finding_line[:100] if finding_line else 'Data collected'}")
                            

                            st.success(f"✅ **Step {step_idx} Completed** - Verified information gathered and ready for synthesis")
                        
                        thinking_steps.append(f"Research Step {step_idx}: Completed")
                    
                    elif key == "writer":
                        with st.expander("Synthesis & Report Generation", expanded=True):
                            st.write("**Synthesis Reasoning:** Analyzing user input context to determine optimal output structure")
                            st.write("**Analysis Process:**")
                            st.write("   • Synthesizing all research findings across multiple sources")
                            st.write("   • Dynamically adapting output format based on user input specificity")
                            st.write("   • Creating contextually relevant account plan structure")
                            st.write("   • Integrating quantitative and qualitative insights")
                            st.write("   • Prioritizing information relevance to user's implied needs")
                            
                            # Update total count from the final state
                            # Check if research_data is in the output (it usually isn't for writer)
                            # or try to get it from the state if available (LangGraph streaming nuances)
                            # For now, we'll just use the length of the research_insights list we built up
                            total_findings_count = len(research_insights)
                            st.info(f"**Data Integration:** {total_findings_count} research phases synthesized into coherent analysis")
                            st.write(f"**Output Optimization:** Tailoring depth vs breadth based on user request pattern")
                            
                            st.success("✅ **Report Generation Completed** - Contextually optimized analysis ready")
                            research_insights.append(f"Synthesized {total_findings_count} research findings into tailored account plan")
                        
                        thinking_steps.append("Report Generation: Completed")
                        final_response = value.get("final_report", "")
                    
                    elif key == "chat_handler":
                        with st.expander("Conversational Response Handler", expanded=True):
                            st.write("**Assistance Mode:**")
                            st.write("   • Providing helpful guidance")
                            st.write("   • Steering toward research capabilities")
                            st.success("✅ **Response Prepared** - Ready to assist user")
                        # Extract text from AIMessage object
                        msgs = value.get("messages", [])
                        if msgs:
                            final_response = msgs[-1].content
                            # Check if this is a rejection message
                            if "cannot process" in final_response.lower() or "only help with" in final_response.lower():
                                # Mark as a warning/rejection
                                st.warning("⚠️ **Input Validation**: Request outside scope")
                        thinking_steps.append("Conversational Response: Completed")
                    
                    elif key == "clarifier":
                        with st.expander("Clarification Needed", expanded=True):
                            st.write("**Reasoning:** User intent is ambiguous or requires more detail")
                            st.write("**Action:** Asking clarifying questions")
                            st.success("✅ **Clarification Request Generated**")
                        
                        msgs = value.get("messages", [])
                        if msgs:
                            final_response = msgs[-1].content
                        thinking_steps.append("Clarification Request: Generated")

        # Display Research Insights Summary
        if research_insights:
            with insights_container:
                with st.expander("Research Summary & Insights Collected", expanded=True):
                    st.write("**Key Information Gathered:**")
                    for i, insight in enumerate(research_insights, 1):
                        st.write(f"{i}. {insight}")
                    st.info(f"**Intelligence Summary:** Processed {len(research_insights)} research phases with comprehensive data collection")

        # Prepare unique sources list for UI + downloadable references
        unique_sources = []
        seen_sources = set()
        for src in sources_used:
            if not isinstance(src, str):
                continue
            cleaned = src.strip()
            if cleaned and cleaned not in seen_sources:
                seen_sources.add(cleaned)
                unique_sources.append(cleaned)

        # Display Final Output AFTER thinking process
        st.markdown("---")
        st.markdown("### **Research Results**")
        
        # Add download button for the report (append references only to download)
        if final_response:
            download_content = final_response
            if unique_sources:
                download_content += "\n\n### References\n" + "\n".join(
                    f"{idx}. {url}" for idx, url in enumerate(unique_sources, 1)
                )
            col1, col2 = st.columns([1, 4])
            with col1:
                st.download_button(
                    label="Download Report",
                    data=download_content,
                    file_name=f"company_research_{user_input.replace(' ', '_')}.md",
                    mime="text/markdown"
                )
        
        # Display the actual results with proper formatting
        if is_refinement:
            st.markdown("**Refined Analysis:**")
        st.markdown(final_response)
        
        # Add sources section for UI context (already handled separately from chat output)
        if unique_sources:
            with st.expander("Sources & References Used", expanded=False):
                st.write("**URLs accessed during research process:**")
                for i, source in enumerate(unique_sources, 1):
                    st.write(f"{i}. [{source}]({source})")
                st.info(f"Total unique sources accessed: {len(unique_sources)}")
        
        if final_response:
            st.session_state.messages.append({"role": "assistant", "content": final_response})
            db.save_message(st.session_state.current_session_id, "assistant", final_response)

# Handle input with immediate session renaming
final_input = None

if st.session_state.pending_input:
    final_input = st.session_state.pending_input
    st.session_state.pending_input = None
elif user_input:
    # Check if this is the first message to trigger rename
    if len(st.session_state.messages) == 0:
        st.session_state.pending_input = user_input
        # Generate title
        new_title = user_input[:30] + "..." if len(user_input) > 30 else user_input
        db.update_session_name(st.session_state.current_session_id, new_title)
        st.rerun()
    else:
        final_input = user_input

if final_input:
    asyncio.run(process_chat(final_input, user_urls))

# Handle refine request - Non-blocking streaming approach
if "refine_request" in st.session_state and st.session_state.refine_request:
    refine_text = st.session_state.refine_request
    st.session_state.refine_request = None  # Clear the request
    
    # Get the last assistant message for context
    last_report = ""
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "assistant":
            last_report = msg["content"]
            break
    
    # Create refinement input
    refined_input = f"Please refine the previous report with this request: {refine_text}\n\nPrevious Report:\n{last_report[:1000]}..."
    
    # Show refinement in progress immediately
    with st.chat_message("user"):
        st.markdown(f"**Refining Report:** {refine_text}")
    refinement_message = f"Refine Report: {refine_text}"
    st.session_state.messages.append({"role": "user", "content": refinement_message})
    db.save_message(st.session_state.current_session_id, "user", refinement_message)
    
    # Process refinement asynchronously
    asyncio.run(process_chat(refined_input, user_urls, is_refinement=True))

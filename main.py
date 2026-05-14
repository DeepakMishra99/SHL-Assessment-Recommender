import os
import requests
import json
import uvicorn
from typing import Annotated, List, TypedDict, Dict
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from pydantic import BaseModel

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from google import genai

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Mount the static files (assuming they are in the same directory as main.py)
# This allows access to script.js and style.css
app.mount("/static", StaticFiles(directory="."), name="static")

# 2. Serve index.html at the root URL
@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")
# --- 1. MODELS & SCHEMAS (From Notebook) ---
# Represents a single message in the array
# --- 1. STRICT API CONTRACT MODELS ---
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    context_extracted: Dict[str, str]
    retrieved_docs: List[dict]
    reply: str
    is_ready: bool
    end_of_conversation: bool

vector_db = None
app_graph = None

# --- 2. SYSTEM PROMPT (From Notebook) ---
SYSTEM_PROMPT = """
You are the SHL Conversational Assessment Recommender. Your goal is to guide recruiters to the right products.

CORE BEHAVIORS:
1. CLARIFICATION: If the query is vague (e.g., "I need a test"), you MUST ask for:
   - Seniority (Graduate, Mid-Pro, Executive)
   - Role/Skills (Java, Leadership, etc.)
2. RECOMMENDATION: Once context is clear, provide 1-10 assessments from the catalog ONLY.
3. COMPARISON: Use descriptions to explain differences between specific tests.
4. SCOPE: Refuse non-SHL topics (legal, general hiring).

RESPONSE FORMAT:
- A brief professional summary.
- A Markdown table: | # | Name | Test Type | Keys | Duration | Languages | URL |
- URLs MUST be in <brackets>.
- Test Type: 'K' for Knowledge/Ability, 'P' for Personality/Behavior.

Grounded only on the provided context. If no context matches, state you couldn't find a specific SHL product.
"""

# --- 3. HELPER FUNCTIONS (From Notebook) ---
def get_test_type(keys):
    if not keys: return "Other"
    keys_str = " ".join(keys).lower()
    if any(k in keys_str for k in ["knowledge", "skills"]): return "K"
    if any(k in keys_str for k in ["personality", "behavior"]): return "P"
    if any(k in keys_str for k in ["ability", "aptitude"]): return "A"
    if any(k in keys_str for k in ["biodata", "situational judgement"]): return "B"
    if any(k in keys_str for k in ["competencies"]): return "C"
    if any(k in keys_str for k in ["development", "360"]): return "D"
    if any(k in keys_str for k in ["assessment", "exercises"]): return "E"
    if any(k in keys_str for k in ["simulations"]): return "S"
    return "Other"

@app.on_event("startup")
async def startup_event():
    global vector_db, app_graph
    print("--- 🚀 Starting Backend Initialization ---")
    
    try:
        if not API_KEY:
            print("❌ ERROR: GEMINI_API_KEY is missing")
            return

        print("📡 Fetching SHL Catalog...")
        url = "https://tcp-us-prod-rnd.shl.com/voiceRater/shl-ai-hiring/shl_product_catalog.json"
        
        # Keep User-Agent to prevent getting blocked
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        
        # Notebook logic: json.loads with strict=False
        try:
            catalog_data = json.loads(response.text, strict=False)
        except Exception as e:
            print(f"Error decoding JSON: {e}")
            catalog_data = []

        print("🧠 Loading Embeddings...")
        documents = []
        for item in catalog_data:
            name = item.get("name", "N/A")
            keys = item.get("keys", [])
            test_type = get_test_type(keys)
            duration = item.get("duration", "N/A")
            languages = ", ".join(item.get("languages", []))
            url_link = item.get("link", "N/A")
            description = item.get("description", "")

            # Exact notebook string interpolation
            page_content = f"Test Name: {name}. Description: {description}"
            metadata = {
                "Name": name,
                "Test Type": test_type,
                "Keys": ", ".join(keys),
                "Duration": duration,
                "Languages": languages,
                "URL": url_link
            }
            documents.append(Document(page_content=page_content, metadata=metadata))

        if documents:
            embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
            vector_db = FAISS.from_documents(documents, embeddings)
            print(f"✅ Successfully stored {len(documents)} documents in FAISS index.")
        else:
            print("❌ No documents found to index.")
            return

        # --- 4. LANGGRAPH INITIALIZATION (From Notebook) ---
        client = genai.Client(api_key=API_KEY)

        def intent_router_node(state: AgentState):
            user_input = state["messages"][-1].content
            check_prompt = f"Does the following request provide enough detail (role/seniority) to search a catalog? Request: {user_input}. Reply ONLY with 'READY' or 'CLARIFY'."
            
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite", 
                contents=check_prompt
            )
            return {"is_ready": "READY" in response.text.upper()}

        def retriever_node(state: AgentState):
            query = state["messages"][-1].content
            docs = vector_db.similarity_search(query, k=10) # Using Notebook k=10
            recommendations = [d.metadata for d in docs]
            return {"retrieved_docs": recommendations}

        def generator_node(state: AgentState):
            docs_context = "\n".join([str(d) for d in state.get("retrieved_docs", [])])
            history = "\n".join([f"{m.type}: {m.content}" for m in state["messages"]])
            
            full_content = f"{SYSTEM_PROMPT}\n\nCATALOG CONTEXT:\n{docs_context}\n\nHISTORY:\n{history}"
            
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite", 
                contents=full_content
            )
            
            return {
                "reply": response.text,
                "messages": [AIMessage(content=response.text)], # Appends history
                "end_of_conversation": "|" in response.text # Usually ends after a table
            }

        workflow = StateGraph(AgentState)
        workflow.add_node("intent_router", intent_router_node)
        workflow.add_node("retriever", retriever_node)
        workflow.add_node("generator", generator_node)
        
        workflow.set_entry_point("intent_router")
        workflow.add_conditional_edges(
            "intent_router",
            lambda x: "retriever" if x.get("is_ready") else "generator",
            {"retriever": "retriever", "generator": "generator"}
        )
        workflow.add_edge("retriever", "generator")
        workflow.add_edge("generator", END)
        
        
        # Make sure it is JUST compile(), no memory saver!
        app_graph = workflow.compile() 
        print("✅ Backend Ready (Stateless API mode)!")
        

    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")

# ---  1: Correct Health Check (Source: ) ---
@app.get("/health")
def health():
    # 'vector_db is not None' returns True once FAISS is loaded
    is_ready = vector_db is not None
    return {
        "status": "ok", 
        "db_ready": is_ready
    }

# --- FIX 2: Strict Contract Formatting in /chat (Source: [cite: 80-91]) ---
@app.post("/chat")
def chat(request: ChatRequest):
    if not app_graph:
        raise HTTPException(status_code=503, detail="Initializing...")
    
    try:
        langchain_messages = []
        for msg in request.messages:
            if msg.role == "user":
                langchain_messages.append(HumanMessage(content=msg.content))
            else:
                langchain_messages.append(AIMessage(content=msg.content))
        
        initial_state = {
            "messages": langchain_messages,
            "is_ready": False,
            "retrieved_docs": [],
            "end_of_conversation": False
        }
        
        final_state = app_graph.invoke(initial_state)
        
        # FIX 3: Recommendation logic (Source: [cite: 92, 93])
        # Recommendations are EMPTY unless the agent is ready and has results.
        recommendations = []
        if final_state.get("is_ready") and final_state.get("retrieved_docs"):
            # Limit to 10 items as per [cite: 58]
            for doc in final_state.get("retrieved_docs", [])[:10]:
                recommendations.append({
                    "name": doc.get("Name", "Unknown"),
                    "url": doc.get("URL", "#"),
                    "test_type": doc.get("Test Type", "Other")
                })
        
        # FIX 4: Final JSON Structure (Source: [cite: 81-91])
        return {
            "reply": final_state.get("reply", ""),
            "recommendations": recommendations,
            "end_of_conversation": final_state.get("end_of_conversation", False)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)

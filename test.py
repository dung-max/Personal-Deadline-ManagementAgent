from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from typing import Literal
class State(TypedDict):
    message: str
    sentiment: str
def analyze_sentiment(state):
    print("---Analyzing Sentiment---")
    text = state["message"].lower()
    
    # Simple sentiment analysis
    if any(word in text for word in ["good", "great", "happy"]):
        return {"sentiment": "positive"}
    if any(word in text for word in ["bad", "sad", "terrible"]):
        return {"sentiment": "negative"}
    return {"sentiment": "neutral"}

def positive_response(state):
    print("---Positive Response---")
    return {"message": "Thank you for the positive feedback! 😊"}

def negative_response(state):
    print("---Negative Response---")
    return {"message": "We’re sorry to hear that. How can we improve? 😞"}
def sentiment_router(state) -> Literal["positive_response", "negative_response"]:
    if state["sentiment"] == "positive":
        return "positive_response"
    return "negative_response" 
# Initialize graph
builder = StateGraph(State)

# Add nodes
builder.add_node("analyze_sentiment", analyze_sentiment)
builder.add_node("positive_response", positive_response)
builder.add_node("negative_response", negative_response)

# Add edges
builder.add_edge(START, "analyze_sentiment")
builder.add_conditional_edges("analyze_sentiment", sentiment_router)
builder.add_edge("positive_response", END)
builder.add_edge("negative_response", END)

# Compile graph
graph = builder.compile()
result = graph.invoke({"message": "I had a great experience!"})

print(result)
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
class State(TypedDict):
    messages:Annotated[list,add_messages]
    plan:str
    results:str
    city: str
    start_date: str
    days: int
    weather: str
    need_input:bool
    attractions: str
    is_travel_request: bool
    normal_reply: str
    travel_intent: str
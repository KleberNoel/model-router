"""Private operations console for agent profiles and router status."""

import os

import requests
import streamlit as st


st.set_page_config(page_title="Agent Control Plane", page_icon="A", layout="wide")
st.title("Agent Control Plane")
st.caption("Profiles configure Goose, Hermes, and direct model runs through model-router.")

base_url = st.sidebar.text_input("Router URL", os.getenv("MODEL_ROUTER_URL", "http://127.0.0.1:4000"))
api_key = st.sidebar.text_input("Router API key", os.getenv("MODEL_ROUTER_API_KEY", ""), type="password")
headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}


def router_get(path: str):
    response = requests.get(f"{base_url.rstrip('/')}{path}", headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def router_post(path: str, payload: dict):
    response = requests.post(
        f"{base_url.rstrip('/')}{path}", headers={**headers, "Content-Type": "application/json"},
        json=payload, timeout=10,
    )
    response.raise_for_status()
    return response.json()


try:
    status = requests.get(f"{base_url.rstrip('/')}/status", timeout=5).json()
    managed = status.get("managed_llama", {})
    st.sidebar.metric("GPU state", managed.get("state", "unknown"))
    st.sidebar.caption(f"Active route: {managed.get('route_name') or 'none'}")
except requests.RequestException as exc:
    st.sidebar.error(f"Router unavailable: {exc}")

if not api_key:
    st.info("Enter a router API key. Admin actions require a JWT, while profile operations use a tenant token.")
    st.stop()

try:
    models = router_get("/v1/models").get("data", [])
    profiles = router_get("/api/v1/profiles")
except requests.RequestException as exc:
    st.error(f"Could not load router data: {exc}")
    st.stop()

left, right = st.columns(2)
with left:
    st.subheader("Available model routes")
    for model in models:
        capabilities = ", ".join(key for key, value in model.get("capabilities", {}).items() if value)
        st.write(f"**{model['id']}**", capabilities or "no capability metadata")

with right:
    st.subheader("Agent profiles")
    if profiles:
        st.dataframe(
            [
                {
                    "name": profile["name"],
                    "runtime": profile["runtime"],
                    "model": profile["model_route"],
                    "permissions": profile["permission_mode"],
                    "memory": profile["memory_scope"],
                }
                for profile in profiles
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No profiles yet.")

st.divider()
st.subheader("Create agent profile")
with st.form("profile"):
    name = st.text_input("Profile name", "goose-coding")
    runtime = st.selectbox("Runtime", ["goose", "hermes", "direct"])
    route_names = [model["id"] for model in models]
    model_route = st.selectbox("Model route", route_names) if route_names else st.text_input("Model route")
    workspace = st.text_input("Workspace", "/home/kleber/model-router" if runtime == "goose" else "")
    tools = st.multiselect("Tools", ["filesystem", "shell", "memory", "todos", "google_tasks", "google_calendar", "google_drive"], default=["memory", "todos"])
    permission_mode = st.selectbox("Permission mode", ["ask", "readonly", "auto"])
    memory_scope = st.selectbox("Memory scope", ["project", "user", "tenant", "run"])
    submitted = st.form_submit_button("Create profile")

if submitted:
    try:
        created = router_post(
            "/api/v1/profiles",
            {
                "name": name,
                "runtime": runtime,
                "model_route": model_route,
                "workspace": workspace or None,
                "tools": tools,
                "permission_mode": permission_mode,
                "memory_scope": memory_scope,
            },
        )
        st.success(f"Created {created['name']}")
        st.rerun()
    except requests.HTTPError as exc:
        st.error(f"Profile creation failed: {exc.response.text}")

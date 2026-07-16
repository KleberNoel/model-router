"""Optional MCP facade for the harness REST API.

Run with ``agent-tools-mcp`` after installing the ``mcp`` extra. The MCP
server deliberately calls the authenticated REST API so tenant visibility and
outbox behavior stay in one place.
"""

import os

import httpx


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.getenv("MODEL_ROUTER_URL", "http://127.0.0.1:4000").rstrip("/"),
        headers={"Authorization": f"Bearer {os.environ['MODEL_ROUTER_API_KEY']}"},
        timeout=30,
    )


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("agent-tools")

    @mcp.tool()
    def memory_search(query: str, limit: int = 20) -> list[dict]:
        """Search shared tenant-visible and user-owned memories."""
        with _client() as client:
            response = client.get("/api/v1/memories", params={"query": query, "limit": limit})
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    def memory_store(subject: str, content: str, scope: str = "project", importance: int = 50) -> dict:
        """Store an explicit durable memory."""
        with _client() as client:
            response = client.post(
                "/api/v1/memories",
                json={"subject": subject, "content": content, "scope": scope, "importance": importance},
            )
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    def todo_list(include_completed: bool = False) -> list[dict]:
        """List canonical TODO items."""
        with _client() as client:
            response = client.get("/api/v1/todos", params={"include_completed": include_completed})
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    def todo_create(title: str, notes: str | None = None, due_at: str | None = None) -> dict:
        """Create a canonical TODO; Google sync is handled by the outbox worker."""
        payload = {"title": title, "notes": notes, "due_at": due_at}
        with _client() as client:
            response = client.post("/api/v1/todos", json=payload)
            response.raise_for_status()
            return response.json()

    @mcp.tool()
    def todo_complete(todo_id: str) -> dict:
        """Complete a canonical TODO."""
        with _client() as client:
            response = client.post(f"/api/v1/todos/{todo_id}/complete")
            response.raise_for_status()
            return response.json()

    mcp.run(transport="stdio")

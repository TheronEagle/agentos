"""Interfaces — how the world talks to AgentOS.

Machine-first, always: a FastAPI app whose OpenAPI schema IS the product
surface, a natural-language front door that compiles to Goals, async
webhook delivery, and a Celery queue for external workers.
"""

"""
Opportunities API Router
=========================

NOTE: The main opportunity endpoints (list, get by ID) are defined inline
in main.py as they were in the production monolith. This router is kept
for any additional opportunity-specific endpoints or future expansion.

The inline endpoints in main.py handle:
- GET /v1/opportunities - List opportunities with filters
- GET /v1/opportunities/{opp_id} - Get single opportunity
"""

from fastapi import APIRouter

router = APIRouter()

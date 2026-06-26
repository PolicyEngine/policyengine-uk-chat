"""The /conversations router — aggregates the store, sharing, and reports
endpoint groups under one prefix.
"""

from fastapi import APIRouter

from conversations.reports import router as reports_router
from conversations.sharing import router as sharing_router
from conversations.store import router as store_router

router = APIRouter()
router.include_router(store_router)
router.include_router(sharing_router)
router.include_router(reports_router)

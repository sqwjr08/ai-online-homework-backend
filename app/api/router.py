from fastapi import APIRouter

from app.api.routes import assignments, auth, classes, questions, submissions, uploads, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(classes.router)
api_router.include_router(questions.router)
api_router.include_router(assignments.router)
api_router.include_router(submissions.router)
api_router.include_router(uploads.router)

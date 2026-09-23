"""
SERVIX - Backend principal
Plataforma de chatbots inteligentes
"""

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
import logging

# =====================================================
# CONFIGURACIÓN INICIAL
# =====================================================

app = FastAPI(title="SERVIX API", version="1.0.0")

# =====================================================
# CORS - Qué dominios pueden usar la API
# =====================================================

ALLOWED_ORIGINS = [
    "http://localhost:5500",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "https://servix.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================
# LOGS
# =====================================================

logging.basicConfig(level=logging.INFO)

# =====================================================
# VARIABLES DE ENTORNO
# =====================================================

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# =====================================================
# ENDPOINT RAÍZ - Para verificar que el servidor funciona
# =====================================================

@app.get("/")
def inicio():
    return {
        "mensaje": "SERVIX API funcionando",
        "version": "1.0.0",
        "estado": "ok"
    }

@app.get("/health")
def health():
    """Endpoint para verificar que el servidor está vivo."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }

# =====================================================
# (PRÓXIMAMENTE: endpoints de registro, login, chatbots, etc.)
# =====================================================

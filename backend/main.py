"""
SERVIX - Backend principal
Plataforma de chatbots inteligentes para negocios

Endpoints:
- POST   /api/registro              Registrar nuevo cliente
- POST   /api/login                 Iniciar sesión
- GET    /api/me                    Datos del cliente logueado
- POST   /api/logout                Cerrar sesión
- GET    /api/chatbots              Listar chatbots del cliente
- POST   /api/chatbots              Crear chatbot
- GET    /api/chatbots/{id}         Ver un chatbot
- PUT    /api/chatbots/{id}         Editar chatbot
- DELETE /api/chatbots/{id}         Eliminar chatbot
- GET    /api/reglas/{chatbot_id}   Listar reglas de un chatbot
- POST   /api/reglas                Crear regla
- PUT    /api/reglas/{id}           Editar regla
- DELETE /api/reglas/{id}           Eliminar regla
- GET    /api/widget/{token}        Config del widget (público)
- POST   /api/chat/{token}          Recibir mensaje del widget (público)
"""

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
import logging
import bcrypt
import secrets
import re
import json
import psycopg2
import psycopg2.extras
import requests

# =====================================================
# CONFIGURACIÓN INICIAL
# =====================================================

app = FastAPI(title="SERVIX API", version="1.0.0")

# CORS
ALLOWED_ORIGINS = [
    "http://localhost:5500",
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "https://servix-one.vercel.app",
    "https://servix.vercel.app",
    "https://servix.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_PUBLISHABLE_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

# =====================================================
# CONEXIÓN A BASE DE DATOS
# =====================================================

def get_connection():
    """Crea conexión a la base de datos PostgreSQL (Supabase)."""
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Base de datos no configurada")
    return psycopg2.connect(DATABASE_URL)

# =====================================================
# FUNCIONES AUXILIARES
# =====================================================

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False

def generar_token() -> str:
    return secrets.token_urlsafe(32)

def generar_token_chatbot() -> str:
    return secrets.token_urlsafe(16)

def validar_email(email: str) -> bool:
    patron = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(patron, email))

def limpiar_html(texto: str) -> str:
    if not texto:
        return ""
    return re.sub(r'<[^>]*>', '', str(texto))

# =====================================================
# AUTENTICACIÓN - Cliente actual
# =====================================================

async def get_current_cliente(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization[7:] if authorization.startswith("Bearer ") else authorization

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, email, nombre_completo, negocio, telefono, plan, estado,
                   fecha_registro, fecha_fin_prueba, proximo_pago, activo
            FROM clientes
            WHERE token_sesion = %s AND token_expiracion > NOW()
        """, (token,))
        cliente = cursor.fetchone()

        if not cliente:
            raise HTTPException(status_code=401, detail="Token inválido o expirado")

        return dict(cliente)
    finally:
        if conn:
            conn.close()

# =====================================================
# MODELOS PYDANTIC
# =====================================================

class RegistroRequest(BaseModel):
    nombre_completo: str
    negocio: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class ChatbotCreate(BaseModel):
    nombre: str
    modo: str = "reglas"
    mensaje_bienvenida: Optional[str] = "¡Hola! ¿En qué te ayudo?"
    color_primario: Optional[str] = "#2e6fd9"
    color_texto: Optional[str] = "#ffffff"
    posicion: Optional[str] = "derecha"

class ChatbotUpdate(BaseModel):
    nombre: Optional[str] = None
    modo: Optional[str] = None
    mensaje_bienvenida: Optional[str] = None
    color_primario: Optional[str] = None
    color_texto: Optional[str] = None
    posicion: Optional[str] = None
    activo: Optional[bool] = None

class ReglaCreate(BaseModel):
    chatbot_id: int
    pregunta: str
    palabras_clave: str
    respuesta: str

class ReglaUpdate(BaseModel):
    pregunta: Optional[str] = None
    palabras_clave: Optional[str] = None
    respuesta: Optional[str] = None
    activa: Optional[bool] = None

class MensajeChat(BaseModel):
    mensaje: str
    sesion_id: Optional[str] = None

# =====================================================
# RAÍZ Y HEALTH
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
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }

# =====================================================
# REGISTRO
# =====================================================

@app.post("/api/registro")
def registro(data: RegistroRequest):
    if not data.nombre_completo or len(data.nombre_completo) < 2:
        raise HTTPException(status_code=400, detail="Nombre completo inválido")

    if not data.negocio or len(data.negocio) < 2:
        raise HTTPException(status_code=400, detail="Nombre del negocio inválido")

    if not validar_email(data.email):
        raise HTTPException(status_code=400, detail="Email inválido")

    if not data.password or len(data.password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM clientes WHERE email = %s", (data.email.lower(),))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Este email ya está registrado")

        password_hash = hash_password(data.password)
        fecha_fin_prueba = datetime.utcnow() + timedelta(days=7)
        token = generar_token()
        token_expiracion = datetime.utcnow() + timedelta(days=30)

        cursor.execute("""
            INSERT INTO clientes (
                email, password_hash, nombre_completo, negocio,
                estado, fecha_registro, fecha_fin_prueba, activo,
                token_sesion, token_expiracion
            )
            VALUES (%s, %s, %s, %s, 'prueba', NOW(), %s, TRUE, %s, %s)
            RETURNING id, email, nombre_completo, negocio, estado, fecha_fin_prueba
        """, (
            data.email.lower(),
            password_hash,
            limpiar_html(data.nombre_completo),
            limpiar_html(data.negocio),
            fecha_fin_prueba,
            token,
            token_expiracion
        ))

        nuevo_cliente = cursor.fetchone()
        conn.commit()

        return {
            "success": True,
            "mensaje": "Cuenta creada correctamente",
            "token": token,
            "cliente": {
                "id": nuevo_cliente[0],
                "email": nuevo_cliente[1],
                "nombre_completo": nuevo_cliente[2],
                "negocio": nuevo_cliente[3],
                "estado": nuevo_cliente[4],
                "fecha_fin_prueba": nuevo_cliente[5].isoformat() if nuevo_cliente[5] else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error en registro: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al crear la cuenta")
    finally:
        if conn:
            conn.close()

# =====================================================
# LOGIN
# =====================================================

@app.post("/api/login")
def login(data: LoginRequest):
    if not data.email or not data.password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT id, email, password_hash, nombre_completo, negocio,
                   estado, plan, fecha_fin_prueba, proximo_pago, activo
            FROM clientes
            WHERE email = %s
        """, (data.email.lower(),))

        cliente = cursor.fetchone()

        if not cliente or not verify_password(data.password, cliente['password_hash']):
            raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

        if not cliente['activo'] and cliente['estado'] == 'cancelado':
            raise HTTPException(status_code=403, detail="Esta cuenta está cancelada")

        token = generar_token()
        token_expiracion = datetime.utcnow() + timedelta(days=30)

        cursor.execute("""
            UPDATE clientes
            SET token_sesion = %s, token_expiracion = %s
            WHERE id = %s
        """, (token, token_expiracion, cliente['id']))
        conn.commit()

        return {
            "success": True,
            "mensaje": "Login exitoso",
            "token": token,
            "cliente": {
                "id": cliente['id'],
                "email": cliente['email'],
                "nombre_completo": cliente['nombre_completo'],
                "negocio": cliente['negocio'],
                "estado": cliente['estado'],
                "plan": cliente['plan'],
                "fecha_fin_prueba": cliente['fecha_fin_prueba'].isoformat() if cliente['fecha_fin_prueba'] else None,
                "proximo_pago": cliente['proximo_pago'].isoformat() if cliente['proximo_pago'] else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error en login: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al iniciar sesión")
    finally:
        if conn:
            conn.close()

# =====================================================
# LOGOUT
# =====================================================

@app.post("/api/logout")
def logout(cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE clientes
            SET token_sesion = NULL, token_expiracion = NULL
            WHERE id = %s
        """, (cliente['id'],))
        conn.commit()
        return {"success": True, "mensaje": "Sesión cerrada"}
    finally:
        if conn:
            conn.close()

# =====================================================
# DATOS DEL CLIENTE ACTUAL
# =====================================================

@app.get("/api/me")
def obtener_me(cliente = Depends(get_current_cliente)):
    return {"success": True, "cliente": cliente}

# =====================================================
# CHATBOTS
# =====================================================

@app.get("/api/chatbots")
def listar_chatbots(cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, nombre, mensaje_bienvenida, color_primario, color_texto,
                   posicion, modo, token, activo, fecha_creacion
            FROM chatbots
            WHERE cliente_id = %s
            ORDER BY fecha_creacion DESC
        """, (cliente['id'],))
        chatbots = cursor.fetchall()

        for cb in chatbots:
            if cb['fecha_creacion']:
                cb['fecha_creacion'] = cb['fecha_creacion'].isoformat()

        return {"success": True, "chatbots": [dict(cb) for cb in chatbots]}
    finally:
        if conn:
            conn.close()

@app.post("/api/chatbots")
def crear_chatbot(data: ChatbotCreate, cliente = Depends(get_current_cliente)):
    if data.modo not in ['reglas', 'ia', 'mixto']:
        raise HTTPException(status_code=400, detail="Modo inválido. Debe ser: reglas, ia o mixto")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM chatbots
            WHERE cliente_id = %s AND activo = TRUE
        """, (cliente['id'],))
        cantidad_actual = cursor.fetchone()[0]

        limite = 1
        if cliente['plan'] == 'pro':
            limite = 3
        elif cliente['plan'] == 'business':
            limite = 10

        if cantidad_actual >= limite:
            raise HTTPException(
                status_code=400,
                detail=f"Tu plan permite máximo {limite} chatbot(s). Mejora tu plan para crear más."
            )

        token_chatbot = generar_token_chatbot()

        cursor.execute("""
            INSERT INTO chatbots (
                cliente_id, nombre, mensaje_bienvenida,
                color_primario, color_texto, posicion, modo, token, activo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id, nombre, token, modo
        """, (
            cliente['id'],
            limpiar_html(data.nombre),
            limpiar_html(data.mensaje_bienvenida or "¡Hola! ¿En qué te ayudo?"),
            data.color_primario or "#2e6fd9",
            data.color_texto or "#ffffff",
            data.posicion or "derecha",
            data.modo,
            token_chatbot
        ))

        nuevo = cursor.fetchone()
        conn.commit()

        return {
            "success": True,
            "mensaje": "Chatbot creado correctamente",
            "chatbot": {
                "id": nuevo[0],
                "nombre": nuevo[1],
                "token": nuevo[2],
                "modo": nuevo[3]
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error creando chatbot: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al crear el chatbot")
    finally:
        if conn:
            conn.close()

@app.get("/api/chatbots/{chatbot_id}")
def obtener_chatbot(chatbot_id: int, cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, nombre, mensaje_bienvenida, color_primario, color_texto,
                   posicion, modo, token, activo, fecha_creacion
            FROM chatbots
            WHERE id = %s AND cliente_id = %s
        """, (chatbot_id, cliente['id']))
        chatbot = cursor.fetchone()

        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        if chatbot['fecha_creacion']:
            chatbot['fecha_creacion'] = chatbot['fecha_creacion'].isoformat()

        return {"success": True, "chatbot": dict(chatbot)}
    finally:
        if conn:
            conn.close()

@app.put("/api/chatbots/{chatbot_id}")
def actualizar_chatbot(chatbot_id: int, data: ChatbotUpdate, cliente = Depends(get_current_cliente)):
    if data.modo and data.modo not in ['reglas', 'ia', 'mixto']:
        raise HTTPException(status_code=400, detail="Modo inválido")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s",
                       (chatbot_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        campos = []
        valores = []
        for campo, valor in data.dict(exclude_unset=True).items():
            if valor is not None:
                if campo in ['color_primario', 'color_texto', 'modo', 'posicion']:
                    campos.append(f"{campo} = %s")
                    valores.append(valor)
                elif campo == 'activo':
                    campos.append(f"{campo} = %s")
                    valores.append(valor)
                else:
                    campos.append(f"{campo} = %s")
                    valores.append(limpiar_html(valor))

        if not campos:
            raise HTTPException(status_code=400, detail="No hay campos para actualizar")

        valores.append(chatbot_id)
        valores.append(cliente['id'])

        cursor.execute(f"""
            UPDATE chatbots
            SET {', '.join(campos)}
            WHERE id = %s AND cliente_id = %s
        """, valores)
        conn.commit()

        return {"success": True, "mensaje": "Chatbot actualizado"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error actualizando chatbot: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al actualizar")
    finally:
        if conn:
            conn.close()

@app.delete("/api/chatbots/{chatbot_id}")
def eliminar_chatbot(chatbot_id: int, cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chatbots WHERE id = %s AND cliente_id = %s",
                       (chatbot_id, cliente['id']))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")
        conn.commit()
        return {"success": True, "mensaje": "Chatbot eliminado"}
    finally:
        if conn:
            conn.close()

# =====================================================
# REGLAS
# =====================================================

@app.get("/api/reglas/{chatbot_id}")
def listar_reglas(chatbot_id: int, cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s",
                       (chatbot_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        cursor.execute("""
            SELECT id, pregunta, palabras_clave, respuesta, orden, activa, fecha_creacion
            FROM reglas
            WHERE chatbot_id = %s
            ORDER BY orden ASC, id ASC
        """, (chatbot_id,))
        reglas = cursor.fetchall()

        for r in reglas:
            if r['fecha_creacion']:
                r['fecha_creacion'] = r['fecha_creacion'].isoformat()

        return {"success": True, "reglas": [dict(r) for r in reglas]}
    finally:
        if conn:
            conn.close()

@app.post("/api/reglas")
def crear_regla(data: ReglaCreate, cliente = Depends(get_current_cliente)):
    if not data.pregunta or not data.respuesta or not data.palabras_clave:
        raise HTTPException(status_code=400, detail="Pregunta, palabras clave y respuesta son obligatorios")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s",
                       (data.chatbot_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        cursor.execute("SELECT COUNT(*) FROM reglas WHERE chatbot_id = %s", (data.chatbot_id,))
        cantidad = cursor.fetchone()[0]

        limite = 20
        if cliente['plan'] == 'pro':
            limite = 100
        elif cliente['plan'] == 'business':
            limite = 99999

        if cantidad >= limite:
            raise HTTPException(
                status_code=400,
                detail=f"Tu plan permite máximo {limite} reglas por chatbot."
            )

        cursor.execute("""
            INSERT INTO reglas (chatbot_id, pregunta, palabras_clave, respuesta, orden)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            data.chatbot_id,
            limpiar_html(data.pregunta),
            limpiar_html(data.palabras_clave),
            limpiar_html(data.respuesta),
            cantidad
        ))

        nueva_id = cursor.fetchone()[0]
        conn.commit()

        return {"success": True, "mensaje": "Regla creada", "regla_id": nueva_id}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error creando regla: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al crear la regla")
    finally:
        if conn:
            conn.close()

@app.put("/api/reglas/{regla_id}")
def actualizar_regla(regla_id: int, data: ReglaUpdate, cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT r.id FROM reglas r
            JOIN chatbots c ON c.id = r.chatbot_id
            WHERE r.id = %s AND c.cliente_id = %s
        """, (regla_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Regla no encontrada")

        campos = []
        valores = []
        for campo, valor in data.dict(exclude_unset=True).items():
            if valor is not None:
                if campo == 'activa':
                    campos.append(f"{campo} = %s")
                    valores.append(valor)
                else:
                    campos.append(f"{campo} = %s")
                    valores.append(limpiar_html(valor))

        if not campos:
            raise HTTPException(status_code=400, detail="No hay campos para actualizar")

        valores.append(regla_id)
        cursor.execute(f"UPDATE reglas SET {', '.join(campos)} WHERE id = %s", valores)
        conn.commit()

        return {"success": True, "mensaje": "Regla actualizada"}
    finally:
        if conn:
            conn.close()

@app.delete("/api/reglas/{regla_id}")
def eliminar_regla(regla_id: int, cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM reglas r
            USING chatbots c
            WHERE r.chatbot_id = c.id AND r.id = %s AND c.cliente_id = %s
        """, (regla_id, cliente['id']))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Regla no encontrada")
        conn.commit()
        return {"success": True, "mensaje": "Regla eliminada"}
    finally:
        if conn:
            conn.close()

# =====================================================
# WIDGET Y CHAT
# =====================================================

@app.get("/api/widget/{token}")
def obtener_widget(token: str):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, nombre, mensaje_bienvenida, color_primario, color_texto,
                   posicion, modo, activo
            FROM chatbots
            WHERE token = %s
        """, (token,))
        chatbot = cursor.fetchone()

        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        return dict(chatbot)
    finally:
        if conn:
            conn.close()

@app.post("/api/chat/{token}")
def chat(token: str, data: MensajeChat, request: Request):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT id, modo, activo, cliente_id
            FROM chatbots
            WHERE token = %s
        """, (token,))
        chatbot = cursor.fetchone()

        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        if not chatbot['activo']:
            raise HTTPException(status_code=403, detail="Este chatbot está desactivado")

        mensaje = data.mensaje.strip()
        if not mensaje or len(mensaje) > 500:
            raise HTTPException(status_code=400, detail="Mensaje inválido")

        sesion_id = data.sesion_id or generar_token()

        respuesta = ""
        modo_respuesta = "reglas"

        if chatbot['modo'] in ['reglas', 'mixto']:
            respuesta = buscar_en_reglas(cursor, chatbot['id'], mensaje)
            if respuesta:
                modo_respuesta = "reglas"

        if not respuesta and chatbot['modo'] in ['ia', 'mixto']:
            respuesta = consultar_gemini(mensaje, chatbot['cliente_id'])
            modo_respuesta = "ia"

        if not respuesta:
            respuesta = "Lo siento, no tengo una respuesta para eso. Intenta con otra pregunta."

        cursor.execute("""
            INSERT INTO conversaciones (
                chatbot_id, sesion_id, mensaje, respuesta, modo_respuesta
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (chatbot['id'], sesion_id, mensaje, respuesta, modo_respuesta))
        conn.commit()

        return {
            "respuesta": respuesta,
            "sesion_id": sesion_id,
            "modo": modo_respuesta
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error en chat: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al procesar el mensaje")
    finally:
        if conn:
            conn.close()

# =====================================================
# FUNCIONES AUXILIARES
# =====================================================

def buscar_en_reglas(cursor, chatbot_id: int, mensaje: str) -> Optional[str]:
    cursor.execute("""
        SELECT pregunta, palabras_clave, respuesta
        FROM reglas
        WHERE chatbot_id = %s AND activa = TRUE
        ORDER BY orden ASC
    """, (chatbot_id,))
    reglas = cursor.fetchall()

    mensaje_lower = mensaje.lower()

    for regla in reglas:
        palabras = [p.strip().lower() for p in regla['palabras_clave'].split(',')]
        for palabra in palabras:
            if palabra and palabra in mensaje_lower:
                return regla['respuesta']

    return None

def consultar_gemini(mensaje: str, cliente_id: int) -> str:
    if not GEMINI_API_KEY:
        return "El servicio de IA no está disponible en este momento."

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [{
                "parts": [{
                    "text": f"Eres un asistente amable de atención al cliente. Responde de forma breve y clara en español. Pregunta del cliente: {mensaje}"
                }]
            }],
            "generationConfig": {
                "maxOutputTokens": 200,
                "temperature": 0.7
            }
        }

        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        if 'candidates' in data and len(data['candidates']) > 0:
            return data['candidates'][0]['content']['parts'][0]['text'].strip()

        return "Lo siento, no pude procesar tu mensaje."

    except Exception as e:
        logging.error(f"Error consultando Gemini: {str(e)}")
        return "Lo siento, hubo un problema al procesar tu mensaje."

# =====================================================
# MANEJO DE ERRORES GLOBAL
# =====================================================

@app.exception_handler(Exception)
async def error_global(request: Request, exc: Exception):
    logging.error(f"Error no manejado: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "detail": "Error interno del servidor"}
    )

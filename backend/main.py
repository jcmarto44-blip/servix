"""
SERVIX - Backend principal
Plataforma de chatbots inteligentes para negocios
"""

from fastapi import FastAPI, HTTPException, Header, Depends, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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

app = FastAPI(title="SERVIX API", version="1.0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_PUBLISHABLE_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or "").strip()
SUPABASE_SERVICE_KEY = (os.getenv("SUPABASE_SERVICE_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

API_PUBLIC_URL = (os.getenv("API_PUBLIC_URL") or "https://servix-9i0u.onrender.com").strip().rstrip("/")

# =====================================================
# CONEXIÓN A BASE DE DATOS
# =====================================================

def get_connection():
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Base de datos no configurada")
    return psycopg2.connect(DATABASE_URL)

# =====================================================
# AUTO-MIGRACIONES (al arrancar)
# =====================================================

def inicializar_base_datos():
    """Crea tabla configuracion y agrega columnas nuevas a chatbots si no existen."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Tabla configuracion (ya existía)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracion (
                id SERIAL PRIMARY KEY,
                banco VARCHAR(150),
                clabe VARCHAR(50),
                beneficiario VARCHAR(200),
                email_soporte VARCHAR(200),
                mensaje_extra TEXT,
                fecha_actualizacion TIMESTAMP DEFAULT NOW()
            )
        """)
        cursor.execute("SELECT COUNT(*) FROM configuracion WHERE id = 1")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO configuracion (id, banco, clabe, beneficiario, email_soporte, mensaje_extra)
                VALUES (1, '', '', '', '', '')
            """)

        # Nuevas columnas en chatbots
        columnas = [
            ("logo_url", "VARCHAR(500)"),
            ("color_burbuja", "VARCHAR(20) DEFAULT '#2e6fd9'"),
            ("color_header", "VARCHAR(20) DEFAULT '#2e6fd9'"),
            ("color_texto_header", "VARCHAR(20) DEFAULT '#ffffff'"),
            ("color_msg_bot", "VARCHAR(20) DEFAULT '#ffffff'"),
            ("color_msg_user", "VARCHAR(20) DEFAULT '#2e6fd9'"),
            ("mensaje_despedida", "TEXT DEFAULT ''"),
            ("url_privacidad", "VARCHAR(500) DEFAULT '/privacidad'"),
        ]

        for nombre, tipo in columnas:
            try:
                cursor.execute(f"ALTER TABLE chatbots ADD COLUMN IF NOT EXISTS {nombre} {tipo}")
            except Exception as e:
                logging.warning(f"No se pudo agregar columna {nombre}: {e}")
                conn.rollback()
                conn = get_connection()
                cursor = conn.cursor()

        conn.commit()
        logging.info("Base de datos inicializada correctamente.")
    except Exception as e:
        logging.error(f"Error inicializando base de datos: {str(e)}")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

@app.on_event("startup")
def on_startup():
    inicializar_base_datos()

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

def verificar_admin(cliente: dict) -> bool:
    return cliente.get('email') == 'admin@servix.com'

def plan_permite_ia(plan: Optional[str]) -> bool:
    return plan in ('pro', 'business')

def subir_a_supabase(nombre_archivo: str, contenido: bytes, content_type: str) -> Optional[str]:
    """Sube un archivo al bucket 'logos' de Supabase Storage y devuelve la URL pública."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logging.error("Supabase no configurado (URL o SERVICE_KEY faltantes)")
        return None

    try:
        url = f"{SUPABASE_URL}/storage/v1/object/logos/{nombre_archivo}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": content_type,
            "x-upsert": "true"
        }
        r = requests.post(url, headers=headers, data=contenido, timeout=30)
        if r.status_code not in (200, 201):
            logging.error(f"Error subiendo a Supabase: {r.status_code} - {r.text}")
            return None

        # URL pública
        url_publica = f"{SUPABASE_URL}/storage/v1/object/public/logos/{nombre_archivo}"
        return url_publica
    except Exception as e:
        logging.error(f"Excepción subiendo a Supabase: {str(e)}")
        return None

def eliminar_de_supabase(nombre_archivo: str) -> bool:
    """Elimina un archivo del bucket 'logos' de Supabase Storage."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/logos/{nombre_archivo}"
        headers = {"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        r = requests.delete(url, headers=headers, timeout=15)
        return r.status_code in (200, 204)
    except Exception as e:
        logging.error(f"Excepción eliminando de Supabase: {str(e)}")
        return False

# =====================================================
# AUTENTICACIÓN
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
    color_burbuja: Optional[str] = None
    color_header: Optional[str] = None
    color_texto_header: Optional[str] = None
    color_msg_bot: Optional[str] = None
    color_msg_user: Optional[str] = None
    mensaje_despedida: Optional[str] = None
    url_privacidad: Optional[str] = None

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

class EstadoUpdate(BaseModel):
    activo: bool

class PlanUpdate(BaseModel):
    plan: str

class MiCuentaUpdate(BaseModel):
    nombre_completo: Optional[str] = None
    negocio: Optional[str] = None
    telefono: Optional[str] = None
    plan: Optional[str] = None
    estado: Optional[str] = None
    password: Optional[str] = None

class ConfiguracionUpdate(BaseModel):
    banco: Optional[str] = None
    clabe: Optional[str] = None
    beneficiario: Optional[str] = None
    email_soporte: Optional[str] = None
    mensaje_extra: Optional[str] = None

# =====================================================
# RAÍZ Y HEALTH
# =====================================================

@app.get("/")
def inicio():
    return {"mensaje": "SERVIX API funcionando", "version": "1.0.4", "estado": "ok"}

@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

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
# CONFIGURACIÓN (DATOS DE CONTRATACIÓN)
# =====================================================

@app.get("/api/admin/configuracion")
def admin_obtener_configuracion(cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT banco, clabe, beneficiario, email_soporte, mensaje_extra, fecha_actualizacion
            FROM configuracion
            WHERE id = 1
        """)
        cfg = cursor.fetchone()
        if not cfg:
            return {"success": True, "configuracion": {"banco": "", "clabe": "", "beneficiario": "", "email_soporte": "", "mensaje_extra": ""}}
        if cfg.get('fecha_actualizacion'):
            cfg['fecha_actualizacion'] = cfg['fecha_actualizacion'].isoformat()
        return {"success": True, "configuracion": dict(cfg)}
    finally:
        if conn:
            conn.close()

@app.put("/api/admin/configuracion")
def admin_actualizar_configuracion(data: ConfiguracionUpdate, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        campos = []
        valores = []

        if data.banco is not None:
            campos.append("banco = %s"); valores.append(limpiar_html(data.banco))
        if data.clabe is not None:
            campos.append("clabe = %s"); valores.append(limpiar_html(data.clabe))
        if data.beneficiario is not None:
            campos.append("beneficiario = %s"); valores.append(limpiar_html(data.beneficiario))
        if data.email_soporte is not None:
            campos.append("email_soporte = %s"); valores.append(limpiar_html(data.email_soporte))
        if data.mensaje_extra is not None:
            campos.append("mensaje_extra = %s"); valores.append(data.mensaje_extra)

        if not campos:
            raise HTTPException(status_code=400, detail="No hay campos para actualizar")

        campos.append("fecha_actualizacion = NOW()")

        cursor.execute(f"UPDATE configuracion SET {', '.join(campos)} WHERE id = 1", valores)
        conn.commit()

        return {"success": True, "mensaje": "Datos de contratación actualizados"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error actualizando configuracion: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al actualizar los datos")
    finally:
        if conn:
            conn.close()

@app.get("/api/configuracion-publica")
def obtener_configuracion_publica():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT banco, clabe, beneficiario, email_soporte, mensaje_extra FROM configuracion WHERE id = 1")
        cfg = cursor.fetchone()
        if not cfg:
            return {"success": True, "configuracion": {"banco": "", "clabe": "", "beneficiario": "", "email_soporte": "", "mensaje_extra": ""}}
        return {"success": True, "configuracion": dict(cfg)}
    finally:
        if conn:
            conn.close()

# =====================================================
# CHATBOTS (cliente)
# =====================================================

@app.get("/api/chatbots")
def listar_chatbots(cliente = Depends(get_current_cliente)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, nombre, mensaje_bienvenida, mensaje_despedida,
                   color_primario, color_texto, color_burbuja, color_header,
                   color_texto_header, color_msg_bot, color_msg_user,
                   logo_url, url_privacidad,
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

    if data.modo in ('ia', 'mixto') and not plan_permite_ia(cliente.get('plan')):
        raise HTTPException(status_code=403, detail="El modo IA está disponible solo en los planes Pro y Business. Mejora tu plan para usarlo.")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM chatbots WHERE cliente_id = %s AND activo = TRUE", (cliente['id'],))
        cantidad_actual = cursor.fetchone()[0]

        limite = 1
        if cliente['plan'] == 'pro':
            limite = 3
        elif cliente['plan'] == 'business':
            limite = 10

        if cantidad_actual >= limite:
            raise HTTPException(status_code=400, detail=f"Tu plan permite máximo {limite} chatbot(s). Mejora tu plan para crear más.")

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
            "chatbot": {"id": nuevo[0], "nombre": nuevo[1], "token": nuevo[2], "modo": nuevo[3]}
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
            SELECT id, nombre, mensaje_bienvenida, mensaje_despedida,
                   color_primario, color_texto, color_burbuja, color_header,
                   color_texto_header, color_msg_bot, color_msg_user,
                   logo_url, url_privacidad,
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

    if data.modo in ('ia', 'mixto') and not plan_permite_ia(cliente.get('plan')):
        raise HTTPException(status_code=403, detail="El modo IA está disponible solo en los planes Pro y Business. Mejora tu plan para usarlo.")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s", (chatbot_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        # Campos que NO se limpian HTML
        campos_crudos = [
            'color_primario', 'color_texto', 'modo', 'posicion',
            'color_burbuja', 'color_header', 'color_texto_header',
            'color_msg_bot', 'color_msg_user', 'url_privacidad'
        ]

        campos = []
        valores = []
        for campo, valor in data.dict(exclude_unset=True).items():
            if valor is not None:
                if campo in campos_crudos:
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

        cursor.execute(f"UPDATE chatbots SET {', '.join(campos)} WHERE id = %s AND cliente_id = %s", valores)
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
        cursor.execute("DELETE FROM chatbots WHERE id = %s AND cliente_id = %s", (chatbot_id, cliente['id']))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")
        conn.commit()
        return {"success": True, "mensaje": "Chatbot eliminado"}
    finally:
        if conn:
            conn.close()

# =====================================================
# SUBIR / ELIMINAR LOGO DEL CHATBOT
# =====================================================

@app.post("/api/chatbots/{chatbot_id}/logo")
async def subir_logo_chatbot(chatbot_id: int, file: UploadFile = File(...), cliente = Depends(get_current_cliente)):
    """Sube el logo del chatbot a Supabase Storage (bucket 'logos')."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s", (chatbot_id, cliente['id']))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        # Validar tipo
        if file.content_type not in ('image/jpeg', 'image/png', 'image/webp', 'image/svg+xml'):
            raise HTTPException(status_code=400, detail="Formato no permitido. Usa JPG, PNG, WEBP o SVG.")

        contenido = await file.read()

        # Validar tamaño (2 MB)
        if len(contenido) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="El archivo supera los 2 MB.")

        # Nombre único
        ext = file.filename.split('.')[-1].lower() if '.' in file.filename else 'png'
        nombre = f"chatbot_{chatbot_id}_{secrets.token_hex(6)}.{ext}"

        # Subir a Supabase
        url = subir_a_supabase(nombre, contenido, file.content_type)
        if not url:
            raise HTTPException(status_code=500, detail="No se pudo subir el logo a Supabase.")

        # Guardar en BD
        cursor.execute("UPDATE chatbots SET logo_url = %s WHERE id = %s", (url, chatbot_id))
        conn.commit()

        return {"success": True, "mensaje": "Logo subido correctamente", "logo_url": url}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error subiendo logo: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al subir el logo")
    finally:
        if conn:
            conn.close()

@app.delete("/api/chatbots/{chatbot_id}/logo")
def eliminar_logo_chatbot(chatbot_id: int, cliente = Depends(get_current_cliente)):
    """Elimina el logo del chatbot."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("SELECT logo_url FROM chatbots WHERE id = %s AND cliente_id = %s", (chatbot_id, cliente['id']))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        if row.get('logo_url'):
            nombre = row['logo_url'].split('/')[-1]
            eliminar_de_supabase(nombre)

        cursor.execute("UPDATE chatbots SET logo_url = NULL WHERE id = %s", (chatbot_id,))
        conn.commit()

        return {"success": True, "mensaje": "Logo eliminado"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error eliminando logo: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al eliminar el logo")
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

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s", (chatbot_id, cliente['id']))
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

        cursor.execute("SELECT id FROM chatbots WHERE id = %s AND cliente_id = %s", (data.chatbot_id, cliente['id']))
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
            raise HTTPException(status_code=400, detail=f"Tu plan permite máximo {limite} reglas por chatbot.")

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
                    campos.append(f"{campo} = %s"); valores.append(valor)
                else:
                    campos.append(f"{campo} = %s"); valores.append(limpiar_html(valor))

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
# ADMIN - CLIENTES
# =====================================================

@app.get("/api/admin/clientes")
def admin_listar_clientes(cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT 
                c.id, c.email, c.nombre_completo, c.negocio, c.telefono,
                c.plan, c.estado, c.fecha_registro, c.fecha_fin_prueba,
                c.proximo_pago, c.activo,
                (SELECT COUNT(*) FROM chatbots WHERE cliente_id = c.id) as total_chatbots,
                (SELECT COUNT(*) FROM conversaciones conv 
                 JOIN chatbots ch ON ch.id = conv.chatbot_id 
                 WHERE ch.cliente_id = c.id) as total_conversaciones
            FROM clientes c
            WHERE c.email != 'admin@servix.com'
            ORDER BY c.fecha_registro DESC
        """)
        clientes = cursor.fetchall()

        for cl in clientes:
            if cl['fecha_registro']:
                cl['fecha_registro'] = cl['fecha_registro'].isoformat()
            if cl['fecha_fin_prueba']:
                cl['fecha_fin_prueba'] = cl['fecha_fin_prueba'].isoformat()
            if cl['proximo_pago']:
                cl['proximo_pago'] = cl['proximo_pago'].isoformat()
            cl['total_chatbots'] = cl['total_chatbots'] or 0
            cl['total_conversaciones'] = cl['total_conversaciones'] or 0

        return {"success": True, "clientes": [dict(c) for c in clientes]}
    finally:
        if conn:
            conn.close()

@app.get("/api/admin/clientes/{cliente_id}")
def admin_obtener_cliente(cliente_id: int, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT 
                c.id, c.email, c.nombre_completo, c.negocio, c.telefono,
                c.plan, c.estado, c.fecha_registro, c.fecha_fin_prueba,
                c.proximo_pago, c.activo,
                (SELECT COUNT(*) FROM chatbots WHERE cliente_id = c.id) as total_chatbots
            FROM clientes c
            WHERE c.id = %s
        """, (cliente_id,))

        cl = cursor.fetchone()
        if not cl:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        if cl['fecha_registro']:
            cl['fecha_registro'] = cl['fecha_registro'].isoformat()
        if cl['fecha_fin_prueba']:
            cl['fecha_fin_prueba'] = cl['fecha_fin_prueba'].isoformat()
        if cl['proximo_pago']:
            cl['proximo_pago'] = cl['proximo_pago'].isoformat()
        cl['total_chatbots'] = cl['total_chatbots'] or 0

        return {"success": True, "cliente": dict(cl)}
    finally:
        if conn:
            conn.close()

@app.put("/api/admin/clientes/{cliente_id}/estado")
def admin_cambiar_estado(cliente_id: int, data: EstadoUpdate, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT email FROM clientes WHERE id = %s", (cliente_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        if row[0] == 'admin@servix.com':
            raise HTTPException(status_code=400, detail="No puedes cambiar el estado del administrador")

        nuevo_estado = 'activo' if data.activo else 'suspendido'

        cursor.execute("UPDATE clientes SET activo = %s, estado = %s WHERE id = %s", (data.activo, nuevo_estado, cliente_id))
        conn.commit()

        return {"success": True, "mensaje": f"Cliente {'activado' if data.activo else 'suspendido'} correctamente", "estado": nuevo_estado}
    finally:
        if conn:
            conn.close()

@app.put("/api/admin/clientes/{cliente_id}/plan")
def admin_cambiar_plan(cliente_id: int, data: PlanUpdate, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    if data.plan not in ['starter', 'pro', 'business']:
        raise HTTPException(status_code=400, detail="Plan inválido. Debe ser: starter, pro o business")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT email FROM clientes WHERE id = %s", (cliente_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        cursor.execute("UPDATE clientes SET plan = %s, estado = 'activo', activo = TRUE WHERE id = %s", (data.plan, cliente_id))
        conn.commit()

        return {"success": True, "mensaje": f"Plan cambiado a {data.plan.upper()} correctamente", "plan": data.plan}
    finally:
        if conn:
            conn.close()

@app.delete("/api/admin/clientes/{cliente_id}")
def admin_eliminar_cliente(cliente_id: int, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT email FROM clientes WHERE id = %s", (cliente_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        if row[0] == 'admin@servix.com':
            raise HTTPException(status_code=400, detail="No puedes eliminar al administrador")

        cursor.execute("DELETE FROM clientes WHERE id = %s", (cliente_id,))
        conn.commit()

        return {"success": True, "mensaje": "Cliente eliminado correctamente"}
    finally:
        if conn:
            conn.close()

# =====================================================
# ADMIN - MI CUENTA
# =====================================================

@app.put("/api/admin/mi-cuenta")
def admin_actualizar_mi_cuenta(data: MiCuentaUpdate, cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        campos = []
        valores = []

        if data.nombre_completo is not None:
            campos.append("nombre_completo = %s"); valores.append(limpiar_html(data.nombre_completo))
        if data.negocio is not None:
            campos.append("negocio = %s"); valores.append(limpiar_html(data.negocio))
        if data.telefono is not None:
            campos.append("telefono = %s"); valores.append(limpiar_html(data.telefono))
        if data.plan is not None:
            if data.plan not in ['starter', 'pro', 'business']:
                raise HTTPException(status_code=400, detail="Plan inválido")
            campos.append("plan = %s"); valores.append(data.plan)
        if data.estado is not None:
            if data.estado not in ['prueba', 'activo', 'suspendido', 'cancelado']:
                raise HTTPException(status_code=400, detail="Estado inválido")
            campos.append("estado = %s"); valores.append(data.estado)
        if data.password is not None:
            if len(data.password) < 6:
                raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")
            campos.append("password_hash = %s"); valores.append(hash_password(data.password))

        if not campos:
            raise HTTPException(status_code=400, detail="No hay campos para actualizar")

        valores.append(cliente['id'])

        cursor.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id = %s", valores)
        conn.commit()

        return {"success": True, "mensaje": "Tu cuenta fue actualizada correctamente"}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error actualizando cuenta admin: {str(e)}")
        raise HTTPException(status_code=500, detail="Error al actualizar la cuenta")
    finally:
        if conn:
            conn.close()

# =====================================================
# ADMIN - CHATBOTS DE TODOS LOS CLIENTES
# =====================================================

@app.get("/api/admin/chatbots")
def admin_listar_todos_chatbots(cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT 
                ch.id, ch.nombre, ch.modo, ch.token, ch.activo, ch.fecha_creacion,
                c.id as cliente_id, c.nombre_completo, c.email, c.negocio
            FROM chatbots ch
            JOIN clientes c ON c.id = ch.cliente_id
            ORDER BY ch.fecha_creacion DESC
        """)
        chatbots = cursor.fetchall()

        for cb in chatbots:
            if cb['fecha_creacion']:
                cb['fecha_creacion'] = cb['fecha_creacion'].isoformat()

        return {"success": True, "chatbots": [dict(cb) for cb in chatbots]}
    finally:
        if conn:
            conn.close()

# =====================================================
# ADMIN - CONVERSACIONES
# =====================================================

@app.get("/api/admin/conversaciones")
def admin_listar_conversaciones(cliente = Depends(get_current_cliente), limit: int = 100):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT 
                conv.id, conv.mensaje, conv.respuesta, conv.modo_respuesta, conv.fecha,
                ch.nombre as chatbot_nombre,
                c.nombre_completo, c.email
            FROM conversaciones conv
            JOIN chatbots ch ON ch.id = conv.chatbot_id
            JOIN clientes c ON c.id = ch.cliente_id
            ORDER BY conv.fecha DESC
            LIMIT %s
        """, (limit,))
        convs = cursor.fetchall()

        for c in convs:
            if c['fecha']:
                c['fecha'] = c['fecha'].isoformat()

        return {"success": True, "conversaciones": [dict(c) for c in convs]}
    finally:
        if conn:
            conn.close()

# =====================================================
# ADMIN - FACTURAS
# =====================================================

@app.get("/api/admin/facturas")
def admin_listar_facturas(cliente = Depends(get_current_cliente)):
    if not verificar_admin(cliente):
        raise HTTPException(status_code=403, detail="Acceso denegado")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT 
                f.id, f.monto, f.moneda, f.plan, f.periodo_inicio, f.periodo_fin,
                f.metodo_pago, f.referencia, f.estado, f.fecha_pago, f.notas,
                c.nombre_completo, c.email, c.negocio
            FROM facturas f
            JOIN clientes c ON c.id = f.cliente_id
            ORDER BY f.fecha_pago DESC NULLS LAST, f.id DESC
        """)
        facturas = cursor.fetchall()

        for f in facturas:
            if f['periodo_inicio']:
                f['periodo_inicio'] = f['periodo_inicio'].isoformat()
            if f['periodo_fin']:
                f['periodo_fin'] = f['periodo_fin'].isoformat()
            if f['fecha_pago']:
                f['fecha_pago'] = f['fecha_pago'].isoformat()

        return {"success": True, "facturas": [dict(f) for f in facturas]}
    finally:
        if conn:
            conn.close()

# =====================================================
# WIDGET Y CHAT (público)
# =====================================================

@app.get("/api/widget/{token}")
def obtener_widget(token: str):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT id, nombre, mensaje_bienvenida, mensaje_despedida,
                   color_primario, color_texto, color_burbuja, color_header,
                   color_texto_header, color_msg_bot, color_msg_user,
                   logo_url, url_privacidad,
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

# ---- Widget JS embebible v2 ----

WIDGET_JS_TEMPLATE = r"""
(function () {
  var TOKEN = "__TOKEN__";
  var API_URL = "__API_URL__";
  var config = null;
  var sessionId = null;
  var isOpen = false;
  var avisoCerrado = false;

  function el(tag, styleText, html) {
    var e = document.createElement(tag);
    if (styleText) e.style.cssText = styleText;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function init() {
    fetch(API_URL + "/api/widget/" + TOKEN)
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        config = cfg;
        if (config.activo === false) return;
        buildWidget();
      })
      .catch(function (e) { console.error("SERVIX widget:", e); });
  }

  function buildWidget() {
    var side = config.posicion === "izquierda" ? "left" : "right";
    var colorBurbuja = config.color_burbuja || config.color_primario || "#2e6fd9";
    var colorHeader = config.color_header || config.color_primario || "#2e6fd9";
    var colorTextoHeader = config.color_texto_header || config.color_texto || "#ffffff";
    var colorMsgBot = config.color_msg_bot || "#ffffff";
    var colorMsgUser = config.color_msg_user || config.color_primario || "#2e6fd9";
    var logo = config.logo_url || "";
    var urlPrivacidad = config.url_privacidad || "/privacidad";
    var msgDespedida = config.mensaje_despedida || "";

    // ---- Burbuja flotante ----
    var bubbleContent = '<svg width="26" height="26" viewBox="0 0 24 24" fill="#ffffff">' +
      '<path d="M12 2C6.48 2 2 6.03 2 11c0 2.4 1.05 4.57 2.77 6.15L4 22l5.05-1.35C10 20.86 11 21 12 21c5.52 0 10-4.03 10-9s-4.48-10-10-10z"/></svg>';

    var bubble = el("div",
      "position:fixed;bottom:20px;" + side + ":20px;width:60px;height:60px;" +
      "border-radius:50%;background:" + colorBurbuja + ";box-shadow:0 4px 16px rgba(0,0,0,.25);" +
      "display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:999999;" +
      "transition:transform .2s;",
      bubbleContent
    );
    bubble.id = "servix-bubble";
    bubble.onmouseenter = function () { bubble.style.transform = "scale(1.08)"; };
    bubble.onmouseleave = function () { bubble.style.transform = "scale(1)"; };

    // ---- Ventana del chat ----
    var win = el("div",
      "position:fixed;bottom:92px;" + side + ":20px;width:340px;max-width:92vw;" +
      "height:460px;max-height:70vh;background:#fff;border-radius:14px;" +
      "box-shadow:0 8px 32px rgba(0,0,0,.25);display:none;flex-direction:column;" +
      "overflow:hidden;z-index:999999;font-family:Arial,Helvetica,sans-serif;"
    );
    win.id = "servix-window";

    // Header
    var headerInner = "";
    if (logo) {
      headerInner += '<img src="' + logo + '" style="width:36px;height:36px;object-fit:contain;background:#fff;border-radius:8px;padding:2px;margin-right:10px;flex-shrink:0;">';
    }
    headerInner += '<span style="flex:1;font-weight:bold;font-size:15px;">' + (config.nombre || "Chat") + '</span>';
    headerInner += '<span id="servix-close" style="cursor:pointer;font-size:18px;padding:0 4px;">&#10005;</span>';

    var header = el("div",
      "display:flex;align-items:center;background:" + colorHeader + ";color:" + colorTextoHeader + ";padding:12px 14px;",
      headerInner
    );

    // Body
    var body = el("div", "flex:1;overflow-y:auto;padding:12px;background:#f7f8fa;display:flex;flex-direction:column;");
    body.id = "servix-body";

    // Input
    var inputWrap = el("div", "display:flex;border-top:1px solid #e5e7eb;padding:8px;gap:6px;background:#fff;");
    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Escribe tu mensaje...";
    input.style.cssText = "flex:1;border:1px solid #d3d8de;border-radius:8px;padding:9px 10px;font-size:14px;outline:none;";

    var sendBtn = el("button",
      "background:" + colorHeader + ";color:" + colorTextoHeader + ";border:none;border-radius:8px;padding:0 16px;cursor:pointer;font-weight:bold;font-size:16px;",
      "&#10148;"
    );

    inputWrap.appendChild(input);
    inputWrap.appendChild(sendBtn);

    // Footer (aviso de privacidad)
    var footer = el("div",
      "display:flex;align-items:center;justify-content:space-between;padding:6px 12px;background:#fff;border-top:1px solid #e5e7eb;font-size:11px;color:#7a8794;",
      '<a href="' + urlPrivacidad + '" target="_blank" style="color:#7a8794;text-decoration:underline;">Aviso de privacidad</a>' +
      '<span id="servix-aviso-x" style="cursor:pointer;padding:0 4px;">&#10005;</span>'
    );
    footer.id = "servix-footer";

    win.appendChild(header);
    win.appendChild(body);
    win.appendChild(inputWrap);
    win.appendChild(footer);

    document.body.appendChild(bubble);
    document.body.appendChild(win);

    addMessage(config.mensaje_bienvenida || "¡Hola! ¿En qué te ayudo?", "bot");

    // Abrir/cerrar
    bubble.onclick = function () {
      isOpen = !isOpen;
      win.style.display = isOpen ? "flex" : "none";
      if (isOpen) input.focus();
    };

    // Cerrar aviso de privacidad
    var avisoX = document.getElementById("servix-aviso-x");
    if (avisoX) {
      avisoX.onclick = function () {
        avisoCerrado = true;
        footer.style.display = "none";
      };
    }

    // Cerrar chat con confirmación
    var closeBtn = document.getElementById("servix-close");
    if (closeBtn) {
      closeBtn.onclick = function (e) {
        e.stopPropagation();
        var confirmar = window.confirm("¿Deseas finalizar el chat?\n\nAceptar = FINALIZAR\nCancelar = CONTINUAR");
        if (confirmar) {
          if (msgDespedida) {
            addMessage(msgDespedida, "bot");
          }
          setTimeout(function () {
            sessionId = null;
            body.innerHTML = "";
            addMessage(config.mensaje_bienvenida || "¡Hola! ¿En qué te ayudo?", "bot");
            win.style.display = "none";
            isOpen = false;
          }, 800);
        }
      };
    }

    function addMessage(text, from) {
      var isBot = from === "bot";
      var msg = document.createElement("div");
      msg.style.cssText =
        "max-width:78%;margin-bottom:10px;padding:9px 12px;border-radius:12px;" +
        "font-size:14px;line-height:1.4;word-wrap:break-word;" +
        (isBot
          ? "background:" + colorMsgBot + ";color:#1e2733;border:1px solid #e5e7eb;align-self:flex-start;"
          : "background:" + colorMsgUser + ";color:#ffffff;align-self:flex-end;");
      msg.textContent = text;
      body.appendChild(msg);
      body.scrollTop = body.scrollHeight;
    }

    function send() {
      var texto2 = input.value.trim();
      if (!texto2) return;
      addMessage(texto2, "user");
      input.value = "";
      input.disabled = true;

      fetch(API_URL + "/api/chat/" + TOKEN, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mensaje: texto2, sesion_id: sessionId })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          sessionId = data.sesion_id;
          addMessage(data.respuesta || "...", "bot");
        })
        .catch(function () {
          addMessage("Error de conexión. Intenta de nuevo.", "bot");
        })
        .finally(function () {
          input.disabled = false;
          input.focus();
        });
    }

    sendBtn.onclick = send;
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") send();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""

@app.get("/widget/{token}.js")
def widget_js(token: str):
    contenido = WIDGET_JS_TEMPLATE.replace("__TOKEN__", token).replace("__API_URL__", API_PUBLIC_URL)
    return Response(content=contenido, media_type="application/javascript")

@app.post("/api/chat/{token}")
def chat(token: str, data: MensajeChat, request: Request):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT ch.id, ch.modo, ch.activo, ch.cliente_id,
                   c.plan as cliente_plan,
                   c.estado as cliente_estado,
                   c.activo as cliente_activo,
                   c.fecha_fin_prueba as cliente_fin_prueba
            FROM chatbots ch
            JOIN clientes c ON c.id = ch.cliente_id
            WHERE ch.token = %s
        """, (token,))
        chatbot = cursor.fetchone()

        if not chatbot:
            raise HTTPException(status_code=404, detail="Chatbot no encontrado")

        if not chatbot['activo']:
            raise HTTPException(status_code=403, detail="Este chatbot está desactivado")

        if not chatbot['cliente_activo']:
            raise HTTPException(status_code=403, detail="Este chatbot no está disponible temporalmente")

        if chatbot['cliente_estado'] == 'prueba' and chatbot['cliente_fin_prueba']:
            if chatbot['cliente_fin_prueba'] < datetime.utcnow():
                cursor.execute("UPDATE clientes SET activo = FALSE, estado = 'suspendido' WHERE id = %s", (chatbot['cliente_id'],))
                conn.commit()
                raise HTTPException(status_code=403, detail="La prueba gratuita ha terminado. Contacta a soporte para activar tu plan.")

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
            if plan_permite_ia(chatbot['cliente_plan']):
                respuesta = consultar_gemini(mensaje, chatbot['cliente_id'])
                modo_respuesta = "ia"
            else:
                respuesta = "Lo siento, no tengo una respuesta para eso. Intenta con otra pregunta."

        if not respuesta:
            respuesta = "Lo siento, no tengo una respuesta para eso. Intenta con otra pregunta."

        cursor.execute("""
            INSERT INTO conversaciones (chatbot_id, sesion_id, mensaje, respuesta, modo_respuesta)
            VALUES (%s, %s, %s, %s, %s)
        """, (chatbot['id'], sesion_id, mensaje, respuesta, modo_respuesta))
        conn.commit()

        return {"respuesta": respuesta, "sesion_id": sesion_id, "modo": modo_respuesta}
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [{"parts": [{"text": f"Eres un asistente amable de atención al cliente. Responde de forma breve y clara en español. Pregunta del cliente: {mensaje}"}]}],
            "generationConfig": {"maxOutputTokens": 200, "temperature": 0.7}
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
    return JSONResponse(status_code=500, content={"success": False, "detail": "Error interno del servidor"})

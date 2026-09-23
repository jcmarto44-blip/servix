-- =====================================================
-- SERVIX - Esquema de base de datos
-- =====================================================

-- Tabla 1: clientes
CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    nombre_completo TEXT NOT NULL,
    negocio TEXT,
    telefono TEXT,
    plan TEXT,
    estado TEXT DEFAULT 'prueba',
    fecha_registro TIMESTAMP DEFAULT NOW(),
    fecha_fin_prueba TIMESTAMP,
    proximo_pago TIMESTAMP,
    activo BOOLEAN DEFAULT TRUE
);

-- Tabla 2: chatbots
CREATE TABLE IF NOT EXISTS chatbots (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    nombre TEXT NOT NULL,
    mensaje_bienvenida TEXT DEFAULT '¡Hola! ¿En qué te ayudo?',
    color_primario TEXT DEFAULT '#2e6fd9',
    color_texto TEXT DEFAULT '#ffffff',
    posicion TEXT DEFAULT 'derecha',
    modo TEXT DEFAULT 'reglas',
    token TEXT UNIQUE NOT NULL,
    activo BOOLEAN DEFAULT TRUE,
    fecha_creacion TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chatbots_cliente ON chatbots(cliente_id);
CREATE INDEX IF NOT EXISTS idx_chatbots_token ON chatbots(token);

-- Tabla 3: reglas
CREATE TABLE IF NOT EXISTS reglas (
    id SERIAL PRIMARY KEY,
    chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
    pregunta TEXT NOT NULL,
    palabras_clave TEXT NOT NULL,
    respuesta TEXT NOT NULL,
    orden INTEGER DEFAULT 0,
    activa BOOLEAN DEFAULT TRUE,
    fecha_creacion TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reglas_chatbot ON reglas(chatbot_id);

-- Tabla 4: conversaciones
CREATE TABLE IF NOT EXISTS conversaciones (
    id SERIAL PRIMARY KEY,
    chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
    sesion_id TEXT NOT NULL,
    visitante_id TEXT,
    visitante_nombre TEXT,
    visitante_email TEXT,
    mensaje TEXT NOT NULL,
    respuesta TEXT,
    modo_respuesta TEXT DEFAULT 'reglas',
    archivada BOOLEAN DEFAULT FALSE,
    fecha TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversaciones_chatbot ON conversaciones(chatbot_id);
CREATE INDEX IF NOT EXISTS idx_conversaciones_sesion ON conversaciones(sesion_id);
CREATE INDEX IF NOT EXISTS idx_conversaciones_fecha ON conversaciones(fecha);

-- Tabla 5: facturas
CREATE TABLE IF NOT EXISTS facturas (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    monto NUMERIC(10,2) NOT NULL,
    moneda TEXT DEFAULT 'USD',
    plan TEXT NOT NULL,
    periodo_inicio TIMESTAMP,
    periodo_fin TIMESTAMP,
    metodo_pago TEXT,
    referencia TEXT,
    estado TEXT DEFAULT 'pendiente',
    fecha_pago TIMESTAMP,
    notas TEXT
);

CREATE INDEX IF NOT EXISTS idx_facturas_cliente ON facturas(cliente_id);
CREATE INDEX IF NOT EXISTS idx_facturas_estado ON facturas(estado);

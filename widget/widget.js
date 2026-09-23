/**
 * SERVIX - Widget público del chatbot
 * Este script se pega en la web del cliente.
 * Carga la configuración de su chatbot y lo muestra flotando.
 */

(function() {
  'use strict';

  // Detectar el script actual para saber el token del chatbot
  var scriptTag = document.currentScript || (function() {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  // El token viene en la URL: widget/ABC123.js
  var src = scriptTag.src || '';
  var match = src.match(/\/widget\/([a-zA-Z0-9_-]+)\.js/);
  var TOKEN = match ? match[1] : null;

  if(!TOKEN) {
    console.error('[SERVIX] No se encontró el token del chatbot en la URL.');
    return;
  }

  // URL de la API (cambiar cuando esté en producción)
  var API_URL = 'https://servix.onrender.com';

  // ============ CARGAR CONFIGURACIÓN ============
  fetch(API_URL + '/api/widget/' + TOKEN)
    .then(function(r) { return r.json(); })
    .then(function(config) {
      if(!config || !config.activo) {
        console.warn('[SERVIX] El chatbot no está activo.');
        return;
      }
      renderizarChatbot(config);
    })
    .catch(function(err) {
      console.error('[SERVIX] Error al cargar el chatbot:', err);
    });

  // ============ RENDERIZAR EL CHATBOT ============
  function renderizarChatbot(config) {
    // Estilos
    var estilos = document.createElement('style');
    estilos.textContent = `
      #servix-widget-container {
        position: fixed;
        bottom: 20px;
        ${config.posicion === 'izquierda' ? 'left: 20px;' : 'right: 20px;'}
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      #servix-widget-btn {
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: ${config.color_primario};
        color: ${config.color_texto};
        border: none;
        cursor: pointer;
        box-shadow: 0 4px 16px rgba(0,0,0,0.25);
        font-size: 28px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: transform 0.2s;
      }
      #servix-widget-btn:hover {
        transform: scale(1.08);
      }
      #servix-widget-ventana {
        position: absolute;
        bottom: 80px;
        ${config.posicion === 'izquierda' ? 'left: 0;' : 'right: 0;'}
        width: 360px;
        max-width: calc(100vw - 40px);
        height: 500px;
        max-height: calc(100vh - 120px);
        background: #ffffff;
        border-radius: 14px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.25);
        display: none;
        flex-direction: column;
        overflow: hidden;
      }
      #servix-widget-ventana.open { display: flex; }
      #servix-widget-header {
        background: ${config.color_primario};
        color: ${config.color_texto};
        padding: 16px 18px;
        font-weight: 600;
        font-size: 15px;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      #servix-widget-cerrar {
        background: transparent;
        border: none;
        color: ${config.color_texto};
        font-size: 20px;
        cursor: pointer;
      }
      #servix-widget-mensajes {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
        background: #f9fafb;
      }
      .servix-msg {
        padding: 10px 14px;
        border-radius: 12px;
        font-size: 14px;
        line-height: 1.4;
        margin-bottom: 10px;
        max-width: 80%;
        word-wrap: break-word;
      }
      .servix-msg-bot {
        background: #ffffff;
        color: #1e2733;
        border: 1px solid #e5e7eb;
        align-self: flex-start;
      }
      .servix-msg-user {
        background: ${config.color_primario};
        color: ${config.color_texto};
        margin-left: auto;
        text-align: right;
      }
      #servix-widget-input-area {
        display: flex;
        padding: 10px;
        border-top: 1px solid #e5e7eb;
        background: #ffffff;
      }
      #servix-widget-input {
        flex: 1;
        border: 1px solid #e5e7eb;
        border-radius: 20px;
        padding: 10px 14px;
        font-size: 14px;
        outline: none;
      }
      #servix-widget-input:focus { border-color: ${config.color_primario}; }
      #servix-widget-enviar {
        background: ${config.color_primario};
        color: ${config.color_texto};
        border: none;
        border-radius: 50%;
        width: 40px;
        height: 40px;
        margin-left: 8px;
        cursor: pointer;
        font-size: 16px;
      }
    `;
    document.head.appendChild(estilos);

    // Contenedor
    var cont = document.createElement('div');
    cont.id = 'servix-widget-container';
    cont.innerHTML = `
      <div id="servix-widget-ventana">
        <div id="servix-widget-header">
          <span>${config.nombre || 'Asistente'}</span>
          <button id="servix-widget-cerrar">✕</button>
        </div>
        <div id="servix-widget-mensajes"></div>
        <div id="servix-widget-input-area">
          <input id="servix-widget-input" type="text" placeholder="Escribe tu mensaje..." />
          <button id="servix-widget-enviar">➤</button>
        </div>
      </div>
      <button id="servix-widget-btn">💬</button>
    `;
    document.body.appendChild(cont);

    // Eventos
    var btn = document.getElementById('servix-widget-btn');
    var ventana = document.getElementById('servix-widget-ventana');
    var cerrar = document.getElementById('servix-widget-cerrar');
    var input = document.getElementById('servix-widget-input');
    var enviar = document.getElementById('servix-widget-enviar');
    var mensajes = document.getElementById('servix-widget-mensajes');

    btn.onclick = function() {
      ventana.classList.toggle('open');
      if(ventana.classList.contains('open') && mensajes.children.length === 0) {
        agregarMensaje('bot', config.mensaje_bienvenida || '¡Hola! ¿En qué te ayudo?');
      }
    };
    cerrar.onclick = function() { ventana.classList.remove('open'); };

    function agregarMensaje(tipo, texto) {
      var div = document.createElement('div');
      div.className = 'servix-msg servix-msg-' + tipo;
      div.textContent = texto;
      mensajes.appendChild(div);
      mensajes.scrollTop = mensajes.scrollHeight;
    }

    function enviarMensaje() {
      var texto = input.value.trim();
      if(!texto) return;
      agregarMensaje('user', texto);
      input.value = '';

      fetch(API_URL + '/api/chat/' + TOKEN, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mensaje: texto })
      })
        .then(function(r) { return r.json(); })
        .then(function(data) {
          agregarMensaje('bot', data.respuesta || 'Lo siento, no entendí.');
        })
        .catch(function() {
          agregarMensaje('bot', 'Error de conexión. Intenta de nuevo.');
        });
    }

    enviar.onclick = enviarMensaje;
    input.addEventListener('keypress', function(e) {
      if(e.key === 'Enter') enviarMensaje();
    });
  }
})();

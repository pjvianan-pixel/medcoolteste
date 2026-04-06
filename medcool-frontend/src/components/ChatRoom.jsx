import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { ChevronLeft, Send, Loader2, AlertCircle } from 'lucide-react'

const API = 'http://localhost:8000'

export default function ChatRoom({ consult, user, token, onBack }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [wsStatus, setWsStatus] = useState('connecting') // connecting | open | closed | error
  const [historyLoading, setHistoryLoading] = useState(true)
  const [error, setError] = useState('')
  const wsRef = useRef(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  // Fetch message history
  useEffect(() => {
    const endpoint =
      user.role === 'professional'
        ? `${API}/professionals/me/consult-requests/${consult.id}/messages`
        : `${API}/patients/me/consult-requests/${consult.id}/messages`

    axios
      .get(endpoint, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .then((res) => {
        const msgs = res.data?.messages ?? res.data ?? []
        setMessages(Array.isArray(msgs) ? msgs : [])
      })
      .catch(() => {
        // History may not exist yet — that's okay
      })
      .finally(() => {
        setHistoryLoading(false)
      })
  }, [consult.id, user.role, token])

  // Connect WebSocket
  useEffect(() => {
    const wsUrl = `ws://localhost:8000/ws/chat/consults/${consult.id}?token=${token}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => setWsStatus('open')
    ws.onclose = () => setWsStatus('closed')
    ws.onerror = () => {
      setWsStatus('error')
      setError('Não foi possível conectar ao chat em tempo real.')
    }
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'message' && data.message) {
          setMessages((prev) => {
            // Deduplicate by id
            const exists = prev.some((m) => m.id === data.message.id)
            return exists ? prev : [...prev, data.message]
          })
        } else if (data.type === 'error') {
          setError(data.detail || 'Erro no chat.')
        }
      } catch {
        // ignore malformed frames
      }
    }

    return () => {
      ws.close()
    }
  }, [consult.id, token])

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = () => {
    const text = input.trim()
    if (!text || wsStatus !== 'open') return

    const clientMessageId =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2) + Date.now().toString(36)
    wsRef.current.send(
      JSON.stringify({
        type: 'message',
        content: text,
        client_message_id: clientMessageId,
      }),
    )
    setInput('')
    inputRef.current?.focus()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const isOwn = (msg) =>
    msg.sender_user_id === user.id ||
    msg.sender_role === user.role

  const statusDot = {
    connecting: 'bg-yellow-400',
    open: 'bg-green-400',
    closed: 'bg-gray-400',
    error: 'bg-red-400',
  }[wsStatus]

  const statusLabel = {
    connecting: 'Conectando...',
    open: 'Conectado',
    closed: 'Desconectado',
    error: 'Erro de conexão',
  }[wsStatus]

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 flex flex-col h-[calc(100vh-10rem)] min-h-[480px]">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200">
        <button
          onClick={onBack}
          className="text-gray-500 hover:text-gray-700 transition-colors"
        >
          <ChevronLeft size={22} />
        </button>
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-gray-800 text-sm truncate">
            {consult.complaint || `Consulta #${consult.id}`}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${statusDot}`} />
          <span className="text-xs text-gray-500">{statusLabel}</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {historyLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="text-blue-500 animate-spin" size={24} />
          </div>
        ) : messages.length === 0 ? (
          <p className="text-center text-gray-400 text-sm py-8">
            Nenhuma mensagem ainda. Seja o primeiro a enviar!
          </p>
        ) : (
          messages.map((msg, idx) => (
            <MessageBubble key={msg.id ?? idx} msg={msg} own={isOwn(msg)} />
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-4 mb-2 flex items-center gap-2 bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2 rounded-lg">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {/* Input */}
      <div className="px-4 py-3 border-t border-gray-200">
        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              wsStatus === 'open' ? 'Digite uma mensagem...' : 'Aguardando conexão...'
            }
            disabled={wsStatus !== 'open'}
            className="flex-1 px-4 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-50 disabled:cursor-not-allowed transition"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || wsStatus !== 'open'}
            className="flex items-center justify-center w-10 h-10 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white rounded-lg transition-colors flex-shrink-0"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ msg, own }) {
  const time = msg.sent_at
    ? new Date(msg.sent_at).toLocaleTimeString('pt-BR', {
        hour: '2-digit',
        minute: '2-digit',
      })
    : ''

  const roleLabel = msg.sender_role === 'professional' ? 'Profissional' : 'Paciente'

  return (
    <div className={`flex flex-col ${own ? 'items-end' : 'items-start'}`}>
      <span className="text-xs text-gray-400 mb-0.5 px-1">{roleLabel}</span>
      <div
        className={`max-w-[75%] px-4 py-2.5 rounded-2xl text-sm ${
          own
            ? 'bg-blue-600 text-white rounded-br-sm'
            : 'bg-gray-100 text-gray-800 rounded-bl-sm'
        }`}
      >
        {msg.content}
      </div>
      {time && (
        <span className="text-xs text-gray-400 mt-0.5 px-1">{time}</span>
      )}
    </div>
  )
}

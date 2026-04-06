import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import api from '../api'
import { ChevronLeft, Send, Loader2, AlertCircle, Wifi, WifiOff } from 'lucide-react'

function formatTime(dateStr) {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleTimeString('pt-BR', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function Chat() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { user, token } = useAuth()

  const consult = location.state?.consult ?? { id }

  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [wsStatus, setWsStatus] = useState('connecting')
  const [historyLoading, setHistoryLoading] = useState(true)
  const [error, setError] = useState('')
  const wsRef = useRef(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  // Fetch message history
  useEffect(() => {
    const endpoint =
      user.role === 'professional'
        ? `/professionals/me/consult-requests/${id}/messages`
        : `/patients/me/consult-requests/${id}/messages`

    api
      .get(endpoint)
      .then((res) => {
        const msgs = res.data?.messages ?? res.data ?? []
        setMessages(Array.isArray(msgs) ? msgs : [])
      })
      .catch(() => {})
      .finally(() => setHistoryLoading(false))
  }, [id, user.role])

  // Connect WebSocket
  useEffect(() => {
    const wsUrl = `ws://localhost:8000/ws/chat/consults/${id}?token=${token}`
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
            const exists = prev.some((m) => m.id === data.message.id)
            return exists ? prev : [...prev, data.message]
          })
        }
      } catch {
        // ignore parse errors
      }
    }

    return () => {
      ws.close()
    }
  }, [id, token])

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = () => {
    const text = input.trim()
    if (!text || wsRef.current?.readyState !== WebSocket.OPEN) return
    wsRef.current.send(
      JSON.stringify({
        type: 'message',
        content: text,
        client_message_id:
          typeof crypto?.randomUUID === 'function'
            ? crypto.randomUUID()
            : Math.random().toString(36).slice(2),
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

  const wsIndicator =
    wsStatus === 'open' ? (
      <span className="flex items-center gap-1 text-green-600 text-xs">
        <Wifi size={13} /> Online
      </span>
    ) : wsStatus === 'connecting' ? (
      <span className="flex items-center gap-1 text-yellow-600 text-xs">
        <Loader2 size={13} className="animate-spin" /> Conectando...
      </span>
    ) : (
      <span className="flex items-center gap-1 text-red-500 text-xs">
        <WifiOff size={13} /> Desconectado
      </span>
    )

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 flex flex-col h-[calc(100vh-10rem)]">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200">
        <button
          onClick={() => navigate(-1)}
          className="text-gray-500 hover:text-gray-700 transition-colors"
        >
          <ChevronLeft size={22} />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-semibold text-gray-800 truncate">
            Chat — {consult.complaint || `Consulta ${id.slice(0, 8)}…`}
          </h2>
        </div>
        {wsIndicator}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {historyLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="text-blue-600 animate-spin" size={28} />
          </div>
        ) : messages.length === 0 ? (
          <p className="text-center text-gray-400 text-sm py-8">
            Nenhuma mensagem ainda. Diga olá!
          </p>
        ) : (
          messages.map((msg, idx) => {
            const isOwn = msg.sender_user_id === user.id
            return (
              <div
                key={msg.id ?? idx}
                className={`flex flex-col ${isOwn ? 'items-end' : 'items-start'}`}
              >
                <span className="text-xs text-gray-400 mb-1">
                  {isOwn ? 'Você' : msg.sender_role === 'professional' ? 'Médico' : 'Paciente'}
                </span>
                <div
                  className={`max-w-xs sm:max-w-sm lg:max-w-md px-4 py-2.5 rounded-2xl text-sm leading-relaxed ${
                    isOwn
                      ? 'bg-blue-600 text-white rounded-br-sm'
                      : 'bg-gray-100 text-gray-800 rounded-bl-sm'
                  }`}
                >
                  {msg.content}
                </div>
                <span className="text-xs text-gray-400 mt-1">
                  {formatTime(msg.sent_at)}
                </span>
              </div>
            )
          })
        )}

        {error && (
          <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-600 text-xs px-3 py-2 rounded-lg">
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-gray-200">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              wsStatus === 'open' ? 'Digite uma mensagem…' : 'Conectando ao chat…'
            }
            disabled={wsStatus !== 'open'}
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            onClick={sendMessage}
            disabled={wsStatus !== 'open' || !input.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white p-2.5 rounded-lg transition-colors"
          >
            <Send size={18} />
          </button>
        </div>
      </div>
    </div>
  )
}

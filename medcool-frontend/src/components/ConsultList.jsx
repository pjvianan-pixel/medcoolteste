import { useState, useEffect } from 'react'
import axios from 'axios'
import { MessageCircle, Video, CreditCard, Loader2, AlertCircle } from 'lucide-react'

const API = 'http://localhost:8000'

const STATUS_LABELS = {
  queued: 'Na fila',
  offering: 'Ofertando',
  matched: 'Em andamento',
  canceled: 'Cancelada',
  expired: 'Expirada',
  cancelled_by_patient: 'Cancelada pelo paciente',
  cancelled_by_professional: 'Cancelada pelo profissional',
  no_show_patient: 'Paciente ausente',
}

const STATUS_COLORS = {
  matched: 'bg-green-100 text-green-700 border-green-200',
  queued: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  offering: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  canceled: 'bg-red-100 text-red-700 border-red-200',
  expired: 'bg-red-100 text-red-700 border-red-200',
  cancelled_by_patient: 'bg-red-100 text-red-700 border-red-200',
  cancelled_by_professional: 'bg-red-100 text-red-700 border-red-200',
  no_show_patient: 'bg-orange-100 text-orange-700 border-orange-200',
}

function formatDate(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function ConsultList({ user, token, onSelectConsult }) {
  const [consults, setConsults] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchConsults()
  }, [])

  const fetchConsults = async () => {
    setLoading(true)
    setError('')
    try {
      const endpoint =
        user.role === 'professional'
          ? `${API}/professionals/me/history`
          : `${API}/patients/me/history`
      const res = await axios.get(endpoint, {
        headers: { Authorization: `Bearer ${token}` },
      })
      setConsults(Array.isArray(res.data) ? res.data : res.data.items ?? [])
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
          'Erro ao carregar consultas. Tente novamente.',
      )
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3">
        <Loader2 className="text-blue-600 animate-spin" size={36} />
        <p className="text-gray-500 text-sm">Carregando consultas...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3">
        <AlertCircle className="text-red-400" size={36} />
        <p className="text-red-500 text-sm">{error}</p>
        <button
          onClick={fetchConsults}
          className="text-blue-600 text-sm underline hover:no-underline"
        >
          Tentar novamente
        </button>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold text-gray-900">
          Minhas Consultas
        </h1>
        <button
          onClick={fetchConsults}
          className="text-xs text-blue-600 hover:underline"
        >
          Atualizar
        </button>
      </div>

      {consults.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-12 text-center">
          <p className="text-gray-400 text-sm">Nenhuma consulta encontrada.</p>
        </div>
      ) : (
        <ul className="space-y-4">
          {consults.map((consult) => (
            <ConsultCard
              key={consult.id}
              consult={consult}
              role={user.role}
              onSelect={onSelectConsult}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function ConsultCard({ consult, role, onSelect }) {
  const { status } = consult
  const badgeClass =
    STATUS_COLORS[status] || 'bg-gray-100 text-gray-600 border-gray-200'
  const label = STATUS_LABELS[status] || status

  const canChat = status === 'matched' || status === 'no_show_patient'
  const canVideo = status === 'matched'
  const canPay = status === 'matched' && role === 'patient'

  return (
    <li className="bg-white rounded-xl shadow-sm border border-gray-200 p-5 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-900 truncate">
            {consult.complaint || 'Consulta #' + consult.id}
          </p>
          <p className="text-xs text-gray-400 mt-1">
            {formatDate(consult.created_at)}
          </p>
          {consult.specialty_id && (
            <p className="text-xs text-gray-500 mt-0.5">
              Especialidade: {consult.specialty_id}
            </p>
          )}
        </div>
        <span
          className={`text-xs font-medium px-2.5 py-1 rounded-full border flex-shrink-0 ${badgeClass}`}
        >
          {label}
        </span>
      </div>

      {(canChat || canVideo || canPay) && (
        <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-gray-100">
          {canChat && (
            <button
              onClick={() => onSelect(consult, 'chat')}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-lg font-medium transition-colors"
            >
              <MessageCircle size={15} />
              Chat
            </button>
          )}
          {canVideo && (
            <button
              onClick={() => onSelect(consult, 'video')}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-purple-50 text-purple-700 hover:bg-purple-100 rounded-lg font-medium transition-colors"
            >
              <Video size={15} />
              Vídeo
            </button>
          )}
          {canPay && (
            <button
              onClick={() => onSelect(consult, 'payment')}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 bg-green-50 text-green-700 hover:bg-green-100 rounded-lg font-medium transition-colors"
            >
              <CreditCard size={15} />
              Pagar
            </button>
          )}
        </div>
      )}
    </li>
  )
}

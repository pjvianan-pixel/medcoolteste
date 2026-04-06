import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import PaymentButton from './PaymentButton'
import { ChevronLeft } from 'lucide-react'

export default function PaymentPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { token } = useAuth()

  const consult = location.state?.consult ?? { id }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate(-1)}
          className="text-gray-500 hover:text-gray-700 transition-colors"
        >
          <ChevronLeft size={22} />
        </button>
        <h2 className="text-lg font-semibold text-gray-800">
          Pagamento da Consulta
        </h2>
      </div>

      <div className="mb-6 p-4 bg-gray-50 rounded-lg">
        <p className="text-sm text-gray-500">Queixa</p>
        <p className="font-medium text-gray-800 mt-1">
          {consult.complaint || `Consulta ${id.slice(0, 8)}…`}
        </p>
      </div>

      <PaymentButton
        consult={consult}
        token={token}
        onPaymentDone={() => navigate('/dashboard', { replace: true })}
      />
    </div>
  )
}

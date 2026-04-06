import { useState } from 'react'
import api from '../api'
import { CreditCard, Loader2, AlertCircle, CheckCircle, ExternalLink } from 'lucide-react'

export default function PaymentButton({ consult, token, onPaymentDone }) {
  const [status, setStatus] = useState('idle') // idle | loading | success | error
  const [errorMsg, setErrorMsg] = useState('')
  const [paymentData, setPaymentData] = useState(null)

  const handlePay = async () => {
    setStatus('loading')
    setErrorMsg('')
    try {
      const res = await api.post(
        `/patients/me/consult-requests/${consult.id}/payments`,
        {},
      )
      setPaymentData(res.data)
      setStatus('success')
      if (res.data?.checkout_url) {
        window.open(res.data.checkout_url, '_blank', 'noopener,noreferrer')
      }
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        'Erro ao processar pagamento.'
      setErrorMsg(typeof detail === 'string' ? detail : JSON.stringify(detail))
      setStatus('error')
    }
  }

  const formatAmount = (cents) => {
    if (!cents && cents !== 0) return null
    return new Intl.NumberFormat('pt-BR', {
      style: 'currency',
      currency: 'BRL',
    }).format(cents / 100)
  }

  return (
    <div className="space-y-4">
      {status === 'idle' && (
        <button
          onClick={handlePay}
          className="flex items-center gap-2 bg-green-600 hover:bg-green-700 text-white font-medium px-6 py-3 rounded-lg transition-colors"
        >
          <CreditCard size={18} />
          Realizar Pagamento
        </button>
      )}

      {status === 'loading' && (
        <div className="flex items-center gap-3 text-gray-600 py-2">
          <Loader2 className="animate-spin" size={20} />
          <span className="text-sm">Processando pagamento...</span>
        </div>
      )}

      {status === 'success' && paymentData && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-green-600">
            <CheckCircle size={22} />
            <span className="font-medium">Pagamento iniciado com sucesso!</span>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 space-y-2 text-sm">
            {paymentData.id && (
              <div className="flex justify-between">
                <span className="text-gray-500">ID do pagamento</span>
                <span className="font-mono text-gray-700 text-xs">{paymentData.id}</span>
              </div>
            )}
            {paymentData.status && (
              <div className="flex justify-between">
                <span className="text-gray-500">Status</span>
                <span className="text-gray-700 capitalize">{paymentData.status}</span>
              </div>
            )}
            {formatAmount(paymentData.amount_cents) && (
              <div className="flex justify-between">
                <span className="text-gray-500">Valor</span>
                <span className="font-semibold text-gray-800">
                  {formatAmount(paymentData.amount_cents)}
                </span>
              </div>
            )}
          </div>

          {paymentData.checkout_url && (
            <a
              href={paymentData.checkout_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-blue-600 hover:underline"
            >
              <ExternalLink size={15} />
              Abrir página de pagamento
            </a>
          )}

          <div className="flex gap-3 pt-2">
            <button
              onClick={onPaymentDone}
              className="text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium px-4 py-2 rounded-lg transition-colors"
            >
              Voltar às consultas
            </button>
          </div>
        </div>
      )}

      {status === 'error' && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-lg">
            <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
          <button
            onClick={() => setStatus('idle')}
            className="text-sm text-blue-600 underline hover:no-underline"
          >
            Tentar novamente
          </button>
        </div>
      )}
    </div>
  )
}

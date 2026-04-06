import { useState, useEffect } from 'react'
import axios from 'axios'
import Login from './components/Login'
import ConsultList from './components/ConsultList'
import ChatRoom from './components/ChatRoom'
import VideoRoom from './components/VideoRoom'
import PaymentButton from './components/PaymentButton'
import {
  Activity,
  LogOut,
  ChevronLeft,
  LayoutDashboard,
} from 'lucide-react'

const API = 'http://localhost:8000'

export default function App() {
  const [token, setToken] = useState(null)
  const [user, setUser] = useState(null)
  const [view, setView] = useState('login')
  const [selectedConsult, setSelectedConsult] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const saved = localStorage.getItem('medcool_token')
    if (saved) {
      fetchMe(saved)
    } else {
      setLoading(false)
    }
  }, [])

  const fetchMe = async (t) => {
    try {
      const res = await axios.get(`${API}/auth/me`, {
        headers: { Authorization: `Bearer ${t}` },
      })
      setToken(t)
      setUser(res.data)
      setView('dashboard')
    } catch {
      localStorage.removeItem('medcool_token')
    } finally {
      setLoading(false)
    }
  }

  const handleLogin = (t, u) => {
    setToken(t)
    setUser(u)
    setView('dashboard')
  }

  const handleLogout = () => {
    localStorage.removeItem('medcool_token')
    setToken(null)
    setUser(null)
    setView('login')
  }

  const handleSelectConsult = (consult, action) => {
    setSelectedConsult(consult)
    if (action === 'chat') setView('chat')
    else if (action === 'video') setView('video')
    else setView('payment')
  }

  const handleBack = () => {
    setSelectedConsult(null)
    setView('dashboard')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <Activity className="text-blue-600 animate-pulse" size={40} />
          <p className="text-gray-500 text-sm">Carregando...</p>
        </div>
      </div>
    )
  }

  if (!token) {
    return <Login onLogin={handleLogin} />
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <button
            onClick={handleBack}
            className="flex items-center gap-2 focus:outline-none"
          >
            <Activity className="text-blue-600" size={28} />
            <span className="text-xl font-bold text-blue-600">MedCool</span>
          </button>
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-600 hidden sm:block truncate max-w-[180px]">
              {user?.email}
            </span>
            <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-full capitalize font-medium">
              {user?.role === 'professional' ? 'Profissional' : 'Paciente'}
            </span>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1 text-sm text-gray-500 hover:text-red-500 transition-colors p-1"
              title="Sair"
            >
              <LogOut size={18} />
              <span className="hidden sm:block">Sair</span>
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 flex gap-6">
        {/* Sidebar — only on dashboard view */}
        {view === 'dashboard' && (
          <aside className="hidden md:block w-56 flex-shrink-0">
            <nav className="bg-white rounded-xl shadow-sm border border-gray-200 p-3">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider px-3 mb-2">
                Menu
              </p>
              <ul className="space-y-1">
                <li>
                  <button
                    onClick={() => setView('dashboard')}
                    className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors bg-blue-50 text-blue-700"
                  >
                    <LayoutDashboard size={18} />
                    Consultas
                  </button>
                </li>
              </ul>
            </nav>
          </aside>
        )}

        {/* Main content */}
        <main className="flex-1 min-w-0">
          {view === 'dashboard' && (
            <ConsultList
              user={user}
              token={token}
              onSelectConsult={handleSelectConsult}
            />
          )}

          {view === 'chat' && selectedConsult && (
            <ChatRoom
              consult={selectedConsult}
              user={user}
              token={token}
              onBack={handleBack}
            />
          )}

          {view === 'video' && selectedConsult && (
            <VideoRoom
              consult={selectedConsult}
              user={user}
              token={token}
              onBack={handleBack}
            />
          )}

          {view === 'payment' && selectedConsult && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
              <div className="flex items-center gap-3 mb-6">
                <button
                  onClick={handleBack}
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
                  {selectedConsult.complaint}
                </p>
              </div>
              <PaymentButton
                consult={selectedConsult}
                token={token}
                onPaymentDone={handleBack}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

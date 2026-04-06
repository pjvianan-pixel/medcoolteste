import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { Activity, LayoutDashboard, LogOut } from 'lucide-react'

export default function Layout({ children }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <NavLink
            to="/dashboard"
            className="flex items-center gap-2 focus:outline-none"
          >
            <Activity className="text-blue-600" size={28} />
            <span className="text-xl font-bold text-blue-600">MedCool</span>
          </NavLink>

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
        {/* Sidebar */}
        <aside className="hidden md:block w-56 flex-shrink-0">
          <nav className="bg-white rounded-xl shadow-sm border border-gray-200 p-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider px-3 mb-2">
              Menu
            </p>
            <ul className="space-y-1">
              <li>
                <NavLink
                  to="/dashboard"
                  className={({ isActive }) =>
                    `w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      isActive
                        ? 'bg-blue-50 text-blue-700'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`
                  }
                >
                  <LayoutDashboard size={18} />
                  Consultas
                </NavLink>
              </li>
            </ul>
          </nav>
        </aside>

        {/* Main content */}
        <main className="flex-1 min-w-0">{children}</main>
      </div>
    </div>
  )
}

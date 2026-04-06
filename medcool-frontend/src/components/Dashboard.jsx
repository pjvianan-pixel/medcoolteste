import ConsultList from './ConsultList'
import { useAuth } from '../context/AuthContext'

export default function Dashboard() {
  const { user, token } = useAuth()

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">
          {user?.role === 'professional'
            ? 'Minhas Consultas — Profissional'
            : 'Minhas Consultas'}
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {user?.role === 'professional'
            ? 'Gerencie suas consultas e interaja com seus pacientes.'
            : 'Acompanhe suas consultas e converse com seu médico.'}
        </p>
      </div>

      <ConsultList user={user} token={token} />
    </div>
  )
}

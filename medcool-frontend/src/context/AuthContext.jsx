import { createContext, useContext, useState, useEffect } from 'react'
import api from '../api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(null)
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const saved = localStorage.getItem('medcool_token')
    if (saved) {
      api
        .get('/auth/me')
        .then((res) => {
          setToken(saved)
          setUser(res.data)
        })
        .catch(() => {
          localStorage.removeItem('medcool_token')
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  const login = (t, u) => {
    setToken(t)
    setUser(u)
  }

  const logout = () => {
    localStorage.removeItem('medcool_token')
    setToken(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ token, user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}

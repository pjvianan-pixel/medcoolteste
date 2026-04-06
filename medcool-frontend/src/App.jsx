import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import Layout from './components/Layout'
import Login from './components/Login'
import Dashboard from './components/Dashboard'
import Chat from './components/Chat'
import VideoCall from './components/VideoCall'
import PaymentPage from './components/PaymentPage'

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />

          {/* Protected */}
          <Route element={<ProtectedRoute />}>
            <Route
              path="/dashboard"
              element={
                <Layout>
                  <Dashboard />
                </Layout>
              }
            />
            <Route
              path="/chat/:id"
              element={
                <Layout>
                  <Chat />
                </Layout>
              }
            />
            <Route
              path="/video/:id"
              element={
                <Layout>
                  <VideoCall />
                </Layout>
              }
            />
            <Route
              path="/payment/:id"
              element={
                <Layout>
                  <PaymentPage />
                </Layout>
              }
            />
          </Route>

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

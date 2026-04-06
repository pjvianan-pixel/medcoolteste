import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import api from '../api'
import Video from 'twilio-video'
import {
  ChevronLeft,
  Mic,
  MicOff,
  Video as VideoIcon,
  VideoOff,
  PhoneOff,
  Loader2,
  AlertCircle,
} from 'lucide-react'

function RemoteParticipant({ participant }) {
  const videoRef = useRef(null)
  const audioRef = useRef(null)

  useEffect(() => {
    const attachTrack = (track) => {
      if (track.kind === 'video' && videoRef.current) {
        videoRef.current.appendChild(track.attach())
      } else if (track.kind === 'audio' && audioRef.current) {
        audioRef.current.appendChild(track.attach())
      }
    }

    participant.tracks.forEach((publication) => {
      if (publication.isSubscribed) attachTrack(publication.track)
    })

    participant.on('trackSubscribed', attachTrack)

    return () => {
      participant.removeAllListeners()
      if (videoRef.current) videoRef.current.innerHTML = ''
      if (audioRef.current) audioRef.current.innerHTML = ''
    }
  }, [participant])

  return (
    <div className="relative bg-gray-900 rounded-xl overflow-hidden aspect-video">
      <div ref={videoRef} className="w-full h-full [&>video]:w-full [&>video]:h-full [&>video]:object-cover" />
      <div ref={audioRef} className="hidden" />
      <div className="absolute bottom-2 left-2 bg-black/50 text-white text-xs px-2 py-1 rounded">
        {participant.identity}
      </div>
    </div>
  )
}

export default function VideoCall() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { user, token } = useAuth()

  const consult = location.state?.consult ?? { id }
  const isProfessional = user.role === 'professional'

  const [sessionStatus, setSessionStatus] = useState('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const [audioMuted, setAudioMuted] = useState(false)
  const [videoMuted, setVideoMuted] = useState(false)
  const [remoteParticipants, setRemoteParticipants] = useState([])

  const roomRef = useRef(null)
  const localVideoRef = useRef(null)
  const localTracksRef = useRef([])

  useEffect(() => {
    return () => disconnectRoom()
  }, [])

  const disconnectRoom = () => {
    if (roomRef.current) {
      roomRef.current.disconnect()
      roomRef.current = null
    }
    localTracksRef.current.forEach((t) => t.stop())
    localTracksRef.current = []
  }

  const startSession = async () => {
    setSessionStatus('loading')
    setErrorMsg('')
    try {
      let accessToken, roomId

      if (isProfessional) {
        const res = await api.post(
          `/professionals/me/consult-requests/${id}/video-session`,
          {},
        )
        accessToken = res.data.access_token
        roomId = res.data.room_id
      } else {
        const res = await api.get(
          `/patients/me/consult-requests/${id}/video-session`,
        )
        accessToken = res.data.access_token
        roomId = res.data.room_id
      }

      if (!accessToken) {
        setErrorMsg(
          'Sessão de vídeo não disponível. Verifique se o Twilio está configurado no servidor.',
        )
        setSessionStatus('error')
        return
      }

      const localTracks = await Video.createLocalTracks({ audio: true, video: { width: 640 } })
      localTracksRef.current = localTracks

      if (localVideoRef.current) {
        localTracks
          .filter((t) => t.kind === 'video')
          .forEach((t) => localVideoRef.current.appendChild(t.attach()))
      }

      const room = await Video.connect(accessToken, {
        name: roomId,
        tracks: localTracks,
      })

      roomRef.current = room

      setRemoteParticipants([...room.participants.values()])

      room.on('participantConnected', (p) => {
        setRemoteParticipants((prev) => [...prev, p])
      })
      room.on('participantDisconnected', (p) => {
        setRemoteParticipants((prev) => prev.filter((x) => x.sid !== p.sid))
      })
      room.on('disconnected', () => {
        setSessionStatus('ended')
        setRemoteParticipants([])
      })

      setSessionStatus('connected')
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        err?.message ||
        'Erro ao iniciar vídeo.'
      setErrorMsg(typeof msg === 'string' ? msg : JSON.stringify(msg))
      setSessionStatus('error')
    }
  }

  const handleHangUp = () => {
    disconnectRoom()
    navigate(-1)
  }

  const toggleAudio = () => {
    localTracksRef.current
      .filter((t) => t.kind === 'audio')
      .forEach((t) => {
        audioMuted ? t.enable() : t.disable()
      })
    setAudioMuted((v) => !v)
  }

  const toggleVideo = () => {
    localTracksRef.current
      .filter((t) => t.kind === 'video')
      .forEach((t) => {
        videoMuted ? t.enable() : t.disable()
      })
    setVideoMuted((v) => !v)
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <button
          onClick={() => navigate(-1)}
          className="text-gray-500 hover:text-gray-700 transition-colors"
        >
          <ChevronLeft size={22} />
        </button>
        <h2 className="text-lg font-semibold text-gray-800">
          Videochamada — {consult.complaint || `Consulta ${id.slice(0, 8)}…`}
        </h2>
      </div>

      {sessionStatus === 'idle' && (
        <div className="text-center py-12">
          <div className="bg-blue-50 rounded-full w-20 h-20 flex items-center justify-center mx-auto mb-4">
            <VideoIcon className="text-blue-600" size={36} />
          </div>
          <p className="text-gray-600 mb-6 text-sm">
            {isProfessional
              ? 'Inicie a sessão de vídeo para o paciente entrar.'
              : 'Aguarde o médico iniciar a sessão de vídeo e clique em entrar.'}
          </p>
          <button
            onClick={startSession}
            className="bg-blue-600 hover:bg-blue-700 text-white font-medium px-8 py-3 rounded-lg transition-colors"
          >
            {isProfessional ? 'Iniciar Vídeo' : 'Entrar na Chamada'}
          </button>
        </div>
      )}

      {sessionStatus === 'loading' && (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <Loader2 className="text-blue-600 animate-spin" size={36} />
          <p className="text-gray-500 text-sm">Conectando à videochamada...</p>
        </div>
      )}

      {sessionStatus === 'error' && (
        <div className="space-y-4">
          <div className="flex items-start gap-3 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
            <AlertCircle size={18} className="flex-shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
          <button
            onClick={() => setSessionStatus('idle')}
            className="text-blue-600 text-sm underline hover:no-underline"
          >
            Tentar novamente
          </button>
        </div>
      )}

      {sessionStatus === 'ended' && (
        <div className="text-center py-12">
          <p className="text-gray-500 text-sm mb-4">Chamada encerrada.</p>
          <button
            onClick={() => navigate(-1)}
            className="bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium px-6 py-2.5 rounded-lg transition-colors text-sm"
          >
            Voltar às consultas
          </button>
        </div>
      )}

      {sessionStatus === 'connected' && (
        <div className="space-y-4">
          {/* Remote videos */}
          {remoteParticipants.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {remoteParticipants.map((p) => (
                <RemoteParticipant key={p.sid} participant={p} />
              ))}
            </div>
          ) : (
            <div className="bg-gray-900 rounded-xl aspect-video flex items-center justify-center">
              <p className="text-gray-400 text-sm">
                Aguardando o outro participante...
              </p>
            </div>
          )}

          {/* Local video (picture-in-picture style) */}
          <div className="relative">
            <div
              ref={localVideoRef}
              className="w-36 rounded-lg overflow-hidden border-2 border-white shadow-lg bg-gray-800 [&>video]:w-full [&>video]:h-full [&>video]:object-cover absolute bottom-0 right-0"
            />
          </div>

          {/* Controls */}
          <div className="flex items-center justify-center gap-4 pt-2">
            <button
              onClick={toggleAudio}
              className={`p-3 rounded-full transition-colors ${
                audioMuted
                  ? 'bg-red-100 text-red-600 hover:bg-red-200'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
              title={audioMuted ? 'Ativar microfone' : 'Silenciar microfone'}
            >
              {audioMuted ? <MicOff size={20} /> : <Mic size={20} />}
            </button>
            <button
              onClick={handleHangUp}
              className="p-3 bg-red-600 hover:bg-red-700 text-white rounded-full transition-colors"
              title="Encerrar chamada"
            >
              <PhoneOff size={20} />
            </button>
            <button
              onClick={toggleVideo}
              className={`p-3 rounded-full transition-colors ${
                videoMuted
                  ? 'bg-red-100 text-red-600 hover:bg-red-200'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
              title={videoMuted ? 'Ativar câmera' : 'Desativar câmera'}
            >
              {videoMuted ? <VideoOff size={20} /> : <VideoIcon size={20} />}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

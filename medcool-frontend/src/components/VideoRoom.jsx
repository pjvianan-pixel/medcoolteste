import { useState, useEffect, useRef } from 'react'
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
import api from '../api'

export default function VideoRoom({ consult, user, token, onBack }) {
  const [sessionStatus, setSessionStatus] = useState('idle') // idle | loading | connected | ended | error
  const [errorMsg, setErrorMsg] = useState('')
  const [room, setRoom] = useState(null)
  const roomRef = useRef(null)
  const [audioMuted, setAudioMuted] = useState(false)
  const [videoMuted, setVideoMuted] = useState(false)
  const [remoteParticipants, setRemoteParticipants] = useState([])

  const localVideoRef = useRef(null)
  const localTracksRef = useRef([])

  const isProfessional = user.role === 'professional'

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnectRoom()
    }
  }, [])

  const disconnectRoom = () => {
    if (roomRef.current) {
      roomRef.current.disconnect()
      roomRef.current = null
    }
    localTracksRef.current.forEach((track) => track.stop())
    localTracksRef.current = []
  }

  const startSession = async () => {
    setSessionStatus('loading')
    setErrorMsg('')
    try {
      let accessToken, roomId

      if (isProfessional) {
        const res = await api.post(
          `/professionals/me/consult-requests/${consult.id}/video-session`,
          {},
        )
        accessToken = res.data.access_token
        roomId = res.data.room_id
      } else {
        const res = await api.get(
          `/patients/me/consult-requests/${consult.id}/video-session`,
        )
        accessToken = res.data.access_token
        roomId = res.data.room_id
      }

      await connectToTwilio(accessToken, roomId)
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        'Erro ao iniciar sessão de vídeo.'
      setErrorMsg(
        typeof detail === 'string' ? detail : JSON.stringify(detail),
      )
      setSessionStatus('error')
    }
  }

  const connectToTwilio = async (accessToken, roomId) => {
    try {
      const localTracks = await Video.createLocalTracks({
        audio: true,
        video: { width: 640 },
      })
      localTracksRef.current = localTracks

      // Attach local video
      const localVideo = localTracks.find((t) => t.kind === 'video')
      if (localVideo && localVideoRef.current) {
        const el = localVideo.attach()
        el.style.width = '100%'
        el.style.borderRadius = '8px'
        localVideoRef.current.innerHTML = ''
        localVideoRef.current.appendChild(el)
      }

      const twilioRoom = await Video.connect(accessToken, {
        name: roomId,
        tracks: localTracks,
      })

      setRoom(twilioRoom)
      roomRef.current = twilioRoom
      setSessionStatus('connected')

      // Attach existing remote participants
      twilioRoom.participants.forEach((participant) =>
        handleParticipantConnected(participant),
      )

      twilioRoom.on('participantConnected', handleParticipantConnected)
      twilioRoom.on('participantDisconnected', handleParticipantDisconnected)
      twilioRoom.on('disconnected', () => setSessionStatus('ended'))
    } catch (err) {
      const msg =
        err?.message || 'Erro ao conectar ao Twilio. Verifique a configuração.'
      setErrorMsg(msg)
      setSessionStatus('error')
      localTracksRef.current.forEach((t) => t.stop())
      localTracksRef.current = []
    }
  }

  const handleParticipantConnected = (participant) => {
    setRemoteParticipants((prev) => [...prev, participant])
    participant.tracks.forEach((pub) => {
      if (pub.isSubscribed) attachRemoteTrack(pub.track, participant.identity)
    })
    participant.on('trackSubscribed', (track) =>
      attachRemoteTrack(track, participant.identity),
    )
    participant.on('trackUnsubscribed', (track) => track.detach())
  }

  const handleParticipantDisconnected = (participant) => {
    setRemoteParticipants((prev) =>
      prev.filter((p) => p.identity !== participant.identity),
    )
  }

  const attachRemoteTrack = (track, identity) => {
    if (track.kind !== 'video' && track.kind !== 'audio') return
    const container = document.getElementById(`remote-${identity}`)
    if (container) {
      const el = track.attach()
      if (track.kind === 'video') {
        el.style.width = '100%'
        el.style.borderRadius = '8px'
      }
      container.appendChild(el)
    }
  }

  const toggleAudio = () => {
    localTracksRef.current
      .filter((t) => t.kind === 'audio')
      .forEach((t) => {
        if (audioMuted) t.enable()
        else t.disable()
      })
    setAudioMuted((m) => !m)
  }

  const toggleVideo = () => {
    localTracksRef.current
      .filter((t) => t.kind === 'video')
      .forEach((t) => {
        if (videoMuted) t.enable()
        else t.disable()
      })
    setVideoMuted((v) => !v)
  }

  const hangUp = () => {
    disconnectRoom()
    setRoom(null)
    setRemoteParticipants([])
    setSessionStatus('ended')
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200">
        <button
          onClick={onBack}
          className="text-gray-500 hover:text-gray-700 transition-colors"
        >
          <ChevronLeft size={22} />
        </button>
        <div className="flex-1">
          <p className="font-semibold text-gray-800 text-sm truncate">
            {consult.complaint || `Consulta #${consult.id}`}
          </p>
          <p className="text-xs text-gray-400">Sessão de Vídeo</p>
        </div>
        {sessionStatus === 'connected' && (
          <span className="flex items-center gap-1 text-xs text-green-600 bg-green-50 border border-green-200 px-2 py-1 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            Ao vivo
          </span>
        )}
      </div>

      <div className="p-6">
        {/* Idle state */}
        {sessionStatus === 'idle' && (
          <div className="flex flex-col items-center justify-center py-16 gap-5">
            <div className="bg-blue-50 rounded-full p-5">
              <VideoIcon className="text-blue-600" size={40} />
            </div>
            <div className="text-center">
              <p className="font-semibold text-gray-800 mb-1">
                {isProfessional ? 'Iniciar consulta por vídeo' : 'Entrar na consulta por vídeo'}
              </p>
              <p className="text-sm text-gray-500">
                {isProfessional
                  ? 'Clique abaixo para criar e iniciar a sessão de vídeo.'
                  : 'Clique abaixo para entrar na sessão de vídeo quando o profissional iniciar.'}
              </p>
            </div>
            <button
              onClick={startSession}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-2.5 rounded-lg transition-colors"
            >
              <VideoIcon size={18} />
              {isProfessional ? 'Iniciar Vídeo' : 'Aguardar / Entrar no Vídeo'}
            </button>
          </div>
        )}

        {/* Loading */}
        {sessionStatus === 'loading' && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <Loader2 className="text-blue-600 animate-spin" size={36} />
            <p className="text-gray-500 text-sm">Conectando...</p>
          </div>
        )}

        {/* Error */}
        {sessionStatus === 'error' && (
          <div className="flex flex-col items-center justify-center py-12 gap-4">
            <AlertCircle className="text-red-400" size={36} />
            <div className="text-center">
              <p className="font-medium text-gray-800 mb-1">
                Erro ao conectar ao vídeo
              </p>
              <p className="text-sm text-red-500 max-w-md">{errorMsg}</p>
            </div>
            <button
              onClick={() => setSessionStatus('idle')}
              className="text-sm text-blue-600 underline hover:no-underline"
            >
              Tentar novamente
            </button>
          </div>
        )}

        {/* Ended */}
        {sessionStatus === 'ended' && (
          <div className="flex flex-col items-center justify-center py-16 gap-4">
            <div className="bg-gray-100 rounded-full p-5">
              <PhoneOff className="text-gray-500" size={32} />
            </div>
            <p className="font-medium text-gray-700">Chamada encerrada</p>
            <button
              onClick={() => setSessionStatus('idle')}
              className="text-sm text-blue-600 underline hover:no-underline"
            >
              Iniciar nova sessão
            </button>
          </div>
        )}

        {/* Connected */}
        {sessionStatus === 'connected' && (
          <div className="flex flex-col gap-4">
            {/* Video grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Local video */}
              <div className="relative bg-gray-900 rounded-xl overflow-hidden aspect-video">
                <div ref={localVideoRef} className="w-full h-full" />
                <span className="absolute bottom-2 left-2 text-xs bg-black/50 text-white px-2 py-0.5 rounded">
                  Você {audioMuted ? '(mudo)' : ''} {videoMuted ? '(vídeo off)' : ''}
                </span>
              </div>

              {/* Remote participants */}
              {remoteParticipants.length === 0 ? (
                <div className="bg-gray-900 rounded-xl aspect-video flex items-center justify-center">
                  <p className="text-gray-400 text-sm">
                    Aguardando participante...
                  </p>
                </div>
              ) : (
                remoteParticipants.map((p) => (
                  <div
                    key={p.identity}
                    className="relative bg-gray-900 rounded-xl overflow-hidden aspect-video"
                  >
                    <div id={`remote-${p.identity}`} className="w-full h-full" />
                    <span className="absolute bottom-2 left-2 text-xs bg-black/50 text-white px-2 py-0.5 rounded">
                      {p.identity}
                    </span>
                  </div>
                ))
              )}
            </div>

            {/* Controls */}
            <div className="flex items-center justify-center gap-3 pt-2">
              <button
                onClick={toggleAudio}
                title={audioMuted ? 'Ativar microfone' : 'Silenciar'}
                className={`flex items-center justify-center w-12 h-12 rounded-full transition-colors ${
                  audioMuted
                    ? 'bg-red-100 text-red-600 hover:bg-red-200'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {audioMuted ? <MicOff size={20} /> : <Mic size={20} />}
              </button>
              <button
                onClick={toggleVideo}
                title={videoMuted ? 'Ativar vídeo' : 'Desativar vídeo'}
                className={`flex items-center justify-center w-12 h-12 rounded-full transition-colors ${
                  videoMuted
                    ? 'bg-red-100 text-red-600 hover:bg-red-200'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {videoMuted ? <VideoOff size={20} /> : <VideoIcon size={20} />}
              </button>
              <button
                onClick={hangUp}
                title="Encerrar chamada"
                className="flex items-center justify-center w-14 h-14 rounded-full bg-red-600 hover:bg-red-700 text-white transition-colors"
              >
                <PhoneOff size={22} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

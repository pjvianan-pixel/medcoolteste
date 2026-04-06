# MedCool — Frontend Telemedicina

React + Vite + Tailwind CSS frontend para a plataforma de telemedicina MedCool.

## Pré-requisitos

- Node.js 18+
- Backend MedCool rodando em `http://localhost:8000`

## Instalação e execução

```bash
cd medcool-frontend
npm install
npm run dev
```

A aplicação estará disponível em `http://localhost:5173`.

## Funcionalidades

| Tela | Descrição |
|------|-----------|
| **Login** | Autenticação com e-mail e senha |
| **Consultas** | Lista de consultas (histórico) do paciente ou profissional |
| **Chat** | Mensagens em tempo real via WebSocket |
| **Vídeo** | Chamada de vídeo via Twilio Video |
| **Pagamento** | Inicia o fluxo de pagamento e abre o checkout |

## Fluxo por papel

### Paciente
1. Faça login com suas credenciais.
2. Na tela de consultas, veja o status de cada consulta.
3. Em uma consulta **matched**:
   - **Chat** — troca de mensagens com o profissional.
   - **Vídeo** — entra na sala de vídeo criada pelo profissional.
   - **Pagar** — inicia o pagamento e redireciona para o checkout.

### Profissional
1. Faça login com suas credenciais.
2. Na tela de consultas, veja o histórico de atendimentos.
3. Em uma consulta **matched**:
   - **Chat** — troca de mensagens com o paciente.
   - **Vídeo** — cria e inicia a sala de vídeo.

## Variáveis de configuração

O endereço do backend é definido diretamente nas constantes `API = 'http://localhost:8000'` nos componentes. Para alterar, edite essa constante nos arquivos abaixo:

- `src/App.jsx`
- `src/components/Login.jsx`
- `src/components/ConsultList.jsx`
- `src/components/ChatRoom.jsx`
- `src/components/VideoRoom.jsx`
- `src/components/PaymentButton.jsx`

O proxy do Vite (em `vite.config.js`) também pode ser usado: rotas `/api/*` são redirecionadas para `http://localhost:8000`.

## Tecnologias utilizadas

- **React 19** — UI
- **Vite 8** — bundler e dev server
- **Tailwind CSS v4** — estilização
- **Axios** — requisições HTTP
- **twilio-video 2.x** — chamadas de vídeo
- **lucide-react** — ícones
- **WebSocket nativo** — chat em tempo real

## Estrutura de diretórios

```
src/
├── components/
│   ├── Login.jsx          # Tela de autenticação
│   ├── ConsultList.jsx    # Lista de consultas
│   ├── ChatRoom.jsx       # Chat em tempo real
│   ├── VideoRoom.jsx      # Sessão de vídeo Twilio
│   └── PaymentButton.jsx  # Botão/fluxo de pagamento
├── App.jsx                # Roteamento e estado global
├── index.css              # Tailwind import
└── main.jsx               # Ponto de entrada React
```

## Possíveis erros

### Vídeo não conecta
O Twilio Video requer variáveis de ambiente configuradas no backend (`TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`). Se não estiver configurado, a tela de vídeo exibirá uma mensagem de erro clara.

### WebSocket de chat
Certifique-se que o backend aceita conexões WebSocket em `ws://localhost:8000/ws/chat/consults/{id}?token={jwt}`.

### CORS
O backend deve permitir requisições de `http://localhost:5173`.

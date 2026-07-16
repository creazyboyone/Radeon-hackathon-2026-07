import { useEffect, useRef, useState } from 'react'

interface AgentEvent {
  type: string
  session_id: string
  kind: string
  content: any
}

const KIND_LABELS: Record<string, string> = {
  reasoning: 'THINKING',
  assistant: 'RESPONSE',
  tool_call: 'TOOL CALL',
  tool_result: 'RESULT',
  user_input: 'INPUT',
  final_answer: 'DONE',
}

const KIND_COLORS: Record<string, string> = {
  reasoning: '#6366f1',
  assistant: '#0ea5e9',
  tool_call: '#f59e0b',
  tool_result: '#10b981',
  user_input: '#6b7280',
  final_answer: '#22c55e',
}

function AgentActivity() {
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const ws = new WebSocket(`ws://${location.host}/ws`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'ping') return
        if (data.type === 'agent_event') {
          setEvents((prev) => [...prev.slice(-200), data])
        }
      } catch {}
    }

    return () => ws.close()
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className="agent-activity">
      <div className="status-bar">
        <span className={`dot ${connected ? 'online' : 'offline'}`} />
        <span>{connected ? 'Connected' : 'Disconnected'}</span>
        <span className="event-count">{events.length} events</span>
        {events.length > 0 && (
          <button className="clear-btn" onClick={() => setEvents([])}>
            Clear
          </button>
        )}
      </div>
      <div className="event-list">
        {events.length === 0 && (
          <div className="empty">Waiting for agent events...</div>
        )}
        {events.map((evt, i) => {
          const label = KIND_LABELS[evt.kind] || evt.kind
          const color = KIND_COLORS[evt.kind] || '#6b7280'
          const content = evt.content || {}
          let text = ''
          if (evt.kind === 'tool_call') {
            text = `${content.name}(${JSON.stringify(content.args || {})})`
          } else if (evt.kind === 'tool_result') {
            const r = content.result || {}
            text = JSON.stringify(r).slice(0, 300)
          } else if (content.text) {
            text = content.text.slice(0, 500)
          } else if (content.tool_calls?.length) {
            text = content.tool_calls
              .map((tc: any) => tc.name)
              .join(', ')
          }
          return (
            <div key={i} className="event-item">
              <span className="event-tag" style={{ background: color }}>
                {label}
              </span>
              <span className="event-session">{evt.session_id?.slice(0, 8)}</span>
              <span className="event-text">{text}</span>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

export default AgentActivity

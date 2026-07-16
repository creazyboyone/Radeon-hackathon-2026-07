import { useEffect, useRef, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'

// ---- 类型 ----
interface Session {
  id: string
  parent_id: string | null
  type: string
  status: string
  trigger: string
  started_at: number
  ended_at: number
}

interface AgentEvent {
  type: string
  session_id: string
  kind: string
  content: any
}

interface HistEvent {
  seq: number
  kind: string
  content: any
  ts: number
}

const KIND_LABELS: Record<string, string> = {
  reasoning: 'THINKING', assistant: 'RESPONSE',
  tool_call: 'TOOL CALL', tool_result: 'RESULT',
  user_input: 'INPUT', final_answer: 'DONE',
}

const KIND_COLORS: Record<string, string> = {
  reasoning: '#6366f1', assistant: '#0ea5e9',
  tool_call: '#f59e0b', tool_result: '#10b981',
  user_input: '#6b7280', final_answer: '#22c55e',
}

// 从 tool_call args 中提取关键信息
function extractCallInfo(name: string, args: any): string {
  const parts: string[] = []
  if (args.service) parts.push(args.service)
  if (args.node) parts.push(args.node)
  if (args.metric) parts.push(args.metric)
  if (args.filter) parts.push(`filter=${args.filter}`)
  if (args.query) parts.push(`"${args.query}"`)
  if (args.action) parts.push(args.action)
  if (args.reason) parts.push(`(${args.reason.slice(0, 60)})`)
  return parts.join(' ')
}

// 从 tool_result 中提取关键摘要
function extractResultSummary(name: string, result: any): string {
  if (!result || typeof result !== 'object') return ''
  if (result.error) return `ERROR: ${result.error}`
  if (result.overall_health) return `health=${result.overall_health} roles=${result.role_count || 0}`
  if (result.count !== undefined) return `count=${result.count}`
  if (result.total_errors !== undefined) return `errors=${result.total_errors} nodes=${result.nodes_checked || 0}`
  if (result.matches !== undefined) return `matches=${result.matches}`
  if (result.result) return `result=${result.result}`
  if (result.command_id) return `cmd_id=${result.command_id} result=${result.result || '?'}`
  if (result.output) return (result.output as string).slice(0, 80).replace(/\n/g, ' ')
  if (result.nodes) {
    const keys = Object.keys(result.nodes)
    return `nodes=${keys.join(',')}`
  }
  return JSON.stringify(result).slice(0, 80)
}

function AgentActivity() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSid, setSelectedSid] = useState<string>('')
  const [events, setEvents] = useState<(HistEvent | AgentEvent)[]>([])
  const [connected, setConnected] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const wsRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 加载 session 列表
  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions')
      const data: Session[] = await res.json()
      setSessions(data)
      // 默认选中最新的 session
      if (data.length > 0 && !selectedSid) {
        setSelectedSid(data[0].id)
      }
    } catch {}
  }, [selectedSid])

  // 加载选中 session 的历史事件
  const fetchEvents = useCallback(async (sid: string) => {
    try {
      const res = await fetch(`/api/sessions/${sid}/events`)
      const data: HistEvent[] = await res.json()
      setEvents(data.map(e => ({ ...e, session_id: sid, type: 'agent_event' })))
    } catch {
      setEvents([])
    }
  }, [])

  useEffect(() => {
    fetchSessions()
    const interval = setInterval(fetchSessions, 5000)
    return () => clearInterval(interval)
  }, [fetchSessions])

  useEffect(() => {
    if (selectedSid) fetchEvents(selectedSid)
  }, [selectedSid, fetchEvents])

  // WebSocket 增量推送
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
          // 只追加到当前选中的 session
          if (data.session_id === selectedSid) {
            setEvents(prev => [...prev.slice(-200), data])
          }
          // 新 session 创建时刷新列表
          if (data.kind === 'user_input') {
            fetchSessions()
          }
        }
      } catch {}
    }
    return () => ws.close()
  }, [selectedSid, fetchSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const toggleExpand = (idx: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  // 构建 session 树
  const roots = sessions.filter(s => !s.parent_id)
  const childrenOf = (pid: string) => sessions.filter(s => s.parent_id === pid)

  const renderSessionTree = (s: Session, depth: number = 0): any => {
    const children = childrenOf(s.id)
    const isSelected = s.id === selectedSid
    const isRunning = !s.ended_at
    return (
      <div key={s.id} className="session-node" style={{ marginLeft: depth * 16 }}>
        <div
          className={`session-item ${isSelected ? 'selected' : ''}`}
          onClick={() => setSelectedSid(s.id)}
        >
          <span className={`session-type type-${s.type}`}>
            {s.type === 'master' ? 'M' : s.type === 'auto' ? 'A' : 'F'}
          </span>
          <span className="session-id">{s.id.slice(0, 8)}</span>
          {isRunning && <span className="session-live">LIVE</span>}
          <span className="session-status">{s.status}</span>
        </div>
        {children.map(c => renderSessionTree(c, depth + 1))}
      </div>
    )
  }

  return (
    <div className="agent-activity">
      <div className="agent-layout">
        {/* 左侧: Session 树 */}
        <div className="session-panel">
          <div className="status-bar">
            <span className={`dot ${connected ? 'online' : 'offline'}`} />
            <span>{connected ? 'Live' : 'Off'}</span>
          </div>
          <div className="session-tree">
            {sessions.length === 0 && <div className="empty">No sessions</div>}
            {roots.map(r => renderSessionTree(r))}
          </div>
        </div>

        {/* 右侧: 事件列表 */}
        <div className="event-panel">
          <div className="status-bar">
            <span>{selectedSid ? `Session ${selectedSid.slice(0, 8)}` : 'No session selected'}</span>
            <span className="event-count">{events.length} events</span>
            {events.length > 0 && (
              <button className="clear-btn" onClick={() => setEvents([])}>Clear</button>
            )}
          </div>
          <div className="event-list">
            {events.length === 0 && <div className="empty">No events</div>}
            {events.map((evt: any, i) => {
              const kind = evt.kind
              const label = KIND_LABELS[kind] || kind
              const color = KIND_COLORS[kind] || '#6b7280'
              const content = evt.content || {}
              const isExpanded = expanded.has(i)
              const isMarkdown = kind === 'reasoning' || kind === 'assistant' || kind === 'final_answer' || kind === 'user_input'
              const isTool = kind === 'tool_call' || kind === 'tool_result'

              let summary = ''
              if (kind === 'tool_call') {
                summary = `${content.name}(${extractCallInfo(content.name, content.args || {})})`
              } else if (kind === 'tool_result') {
                const r = content.result || content
                summary = extractResultSummary(content.name || '', r)
              } else if (content.text) {
                summary = content.text
              } else if (content.tool_calls?.length) {
                summary = content.tool_calls.map((tc: any) => tc.name).join(', ')
              }

              return (
                <div key={i} className={`event-card ${isMarkdown ? 'md-card' : ''}`}>
                  <div className="event-header" onClick={() => isTool && toggleExpand(i)}>
                    <span className="event-tag" style={{ background: color }}>{label}</span>
                    {isTool && <span className="expand-hint">{isExpanded ? '[-]' : '[+]'}</span>}
                    {!isMarkdown && !isTool && <span className="event-summary">{summary}</span>}
                  </div>
                  <div className="event-body">
                    {isMarkdown && (
                      <div className="markdown-body">
                        <ReactMarkdown>{summary || ''}</ReactMarkdown>
                      </div>
                    )}
                    {isTool && isExpanded && (
                      <pre className="event-json">
                        {JSON.stringify(content, null, 2)}
                      </pre>
                    )}
                    {isTool && !isExpanded && (
                      <span className="event-summary">{summary}</span>
                    )}
                  </div>
                </div>
              )
            })}
            <div ref={bottomRef} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default AgentActivity

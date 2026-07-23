import { useEffect, useRef, useState, useCallback, memo } from 'react'
import {
  Typography, Card, Input, Button, Spin, Tag, Tooltip, Badge,
  Empty, Popconfirm, List, Collapse,
} from 'antd'
import {
  SendOutlined, RobotOutlined, UserOutlined, PlusOutlined,
  DeleteOutlined, MessageOutlined, ToolOutlined, BulbOutlined,
  CheckCircleOutlined, ThunderboltOutlined,
  FileTextOutlined, CodeOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { wsManager } from '../websocket'
import { dataCache } from '../dataCache'

const { Text } = Typography

interface ChatSession {
  id: string
  title: string
  created_at: number
  updated_at: number
  msg_count: number
}

interface ChatMsg {
  id: string
  user_msg: string
  status: string
  session_id: string
  reply: string
  role: string
  created_at: number
  processed_at: number
}

interface AgentEvent {
  seq: number
  kind: string
  content: any
  ts: number
}

// ---- 常量 ----
const FLUSH_INTERVAL = 800

// ---- 工具函数 ----

function fmtTime(ts: number): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

const EV_COLORS: Record<string, string> = {
  user_input: '#38bdf8', reasoning: '#818cf8', assistant: '#38bdf8',
  tool_call: '#4ade80', tool_result: '#94a3b8',
  final_answer: '#34d399', error: '#f87171', runbook_prompt: '#22d3ee',
}

const EV_ICONS: Record<string, any> = {
  user_input: <FileTextOutlined />, reasoning: <BulbOutlined />, assistant: <RobotOutlined />,
  tool_call: <ToolOutlined />, tool_result: <CodeOutlined />,
  final_answer: <CheckCircleOutlined />, error: <ThunderboltOutlined />,
  runbook_prompt: <CheckCircleOutlined />,
}

// ---- 单个事件渲染 (纯文本, 不用 Markdown) ----

const EventItem = memo(({ ev }: { ev: AgentEvent }) => {
  const kind = ev.kind
  const c = ev.content || {}
  const color = EV_COLORS[kind] || '#64748b'
  const Icon = EV_ICONS[kind]

  const containerStyle: React.CSSProperties = {
    margin: '3px 0', padding: '6px 8px',
    background: `${color}10`, borderRadius: 4,
    borderLeft: `2px solid ${color}`,
  }

  if (kind === 'user_input') {
    return <div style={{ padding: '2px 0' }}><Text style={{ fontSize: 12, color: '#cbd5e1' }}>{c.message || ''}</Text></div>
  }

  if (kind === 'reasoning') {
    const text = c.text || ''
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
          {Icon} 思考
        </div>
        <div style={{ fontSize: 12, color: '#c7d2fe', lineHeight: 1.4, whiteSpace: 'pre-wrap' }}>{text}</div>
      </div>
    )
  }

  if (kind === 'tool_call') {
    const args = typeof c.arguments === 'string' ? c.arguments : JSON.stringify(c.args || c.arguments || {})
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
          {Icon} 工具: <b style={{ color: '#86efac' }}>{c.name}</b>
        </div>
        <div style={{ fontSize: 10, color: '#86efac', whiteSpace: 'pre-wrap', wordBreak: 'break-all', opacity: 0.7 }}>
          {args}
        </div>
      </div>
    )
  }

  if (kind === 'tool_result') {
    const result = typeof c.result === 'string' ? c.result : JSON.stringify(c.result || {})
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, marginBottom: 2 }}>结果{c.approved === false ? ' (已拦截)' : ''}</div>
        <div style={{ fontSize: 10, color: '#cbd5e1', whiteSpace: 'pre-wrap', wordBreak: 'break-all', opacity: 0.7, maxHeight: 200, overflow: 'auto' }}>
          {result}
        </div>
      </div>
    )
  }

  if (kind === 'assistant') {
    const text = c.content || c.text || ''
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
          {Icon} 响应
        </div>
        <div style={{ fontSize: 12, color: '#e2e8f0', whiteSpace: 'pre-wrap' }}>{text}</div>
      </div>
    )
  }

  if (kind === 'runbook_prompt') {
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4 }}>
          {Icon} 学习闭环
        </div>
      </div>
    )
  }

  if (kind === 'error') {
    return (
      <div style={containerStyle}>
        <div style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
          {Icon} 错误
        </div>
        <div style={{ fontSize: 12, color: '#fca5a5' }}>{c.error || JSON.stringify(c)}</div>
      </div>
    )
  }

  return null
}, (prev, next) => prev.ev === next.ev)  // 只有引用相同时才跳过

// ---- 思考链  ----

function ThinkingTimeline({ events, loading }: { events: AgentEvent[], loading: boolean }) {
  const displayEvents = events.filter(e =>
    !['stream_reasoning', 'stream_content'].includes(e.kind)
  )

  if (displayEvents.length === 0 && !loading) return null


const renderEvents = displayEvents

  return (
    <Collapse
      ghost
      size="small"
      // 默认折叠! 不展开, 不渲染 Timeline DOM
      items={[{
        key: 'thinking',
        label: (
          <span style={{ fontSize: 11, color: '#64748b', display: 'flex', alignItems: 'center', gap: 4 }}>
            {loading ? (
              <><Spin size="small" style={{ marginRight: 4 }} /> Agent 处理中... ({displayEvents.length})</>
            ) : (
              <><BulbOutlined /> 思考过程 ({displayEvents.length})</>
            )}
          </span>
        ),
        children: (
          <div style={{ borderLeft: '2px solid #1e293b', marginLeft: 4, paddingLeft: 8 }}>
            {renderEvents.map((ev, idx) => (
              <EventItem ev={ev} key={`${ev.seq}-${idx}`} />
            ))}
          </div>
        ),
      }]}
    />
  )
}

// ---- 单条消息渲染 (memo 化, 只有 msg 变化才重渲染) ----

const MessageItem = memo(({ msg, events, isProcessing }: {
  msg: ChatMsg
  events: AgentEvent[]
  isProcessing: boolean
}) => {
  const hasEvents = events.length > 0

  return (
    <div style={{ marginBottom: 12 }}>
      {/* 用户消息 */}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginBottom: 8 }}>
        <div style={{
          maxWidth: '70%',
          background: 'linear-gradient(135deg, #0ea5e9, #0284c7)',
          color: '#fff', padding: '10px 14px',
          borderRadius: '12px 12px 2px 12px',
          fontSize: 13, lineHeight: 1.6,
          boxShadow: '0 2px 8px rgba(14, 165, 233, 0.3)',
        }}>
          {msg.user_msg}
          <div style={{ fontSize: 9, opacity: 0.7, marginTop: 4, textAlign: 'right' }}>
            {fmtTime(msg.created_at)}
          </div>
        </div>
        <div style={{
          width: 32, height: 32, flexShrink: 0, borderRadius: '50%', background: '#334155',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
        }}>
          <UserOutlined style={{ color: '#94a3b8', fontSize: 14 }} />
        </div>
      </div>

      {/* Agent 回复 */}
      {(hasEvents || isProcessing || msg.status === 'done' || msg.status === 'error') && (
        <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
          <div style={{
            width: 32, height: 32, flexShrink: 0, borderRadius: '50%', background: '#1e3a5f',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
          }}>
            <RobotOutlined style={{ color: '#38bdf8', fontSize: 14 }} />
          </div>
          <div style={{
            flex: 1, maxWidth: '80%',
            background: '#0f172a', border: '1px solid #1e293b',
            padding: '10px 14px', borderRadius: '12px 12px 12px 2px',
            boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
          }}>
            {(hasEvents || isProcessing) && (
              <ThinkingTimeline events={events} loading={isProcessing && !msg.reply} />
            )}

            {msg.status === 'done' && msg.reply && (
              <div style={{ marginTop: hasEvents ? 8 : 0 }}>
                <div className="markdown-body" style={{ fontSize: 13 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.reply}</ReactMarkdown>
                </div>
              </div>
            )}

            {isProcessing && !hasEvents && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
                <Spin size="small" />
                <span style={{ color: '#64748b', fontSize: 12 }}>
                  {msg.status === 'pending' ? '排队中...' : 'Agent 思考中...'}
                </span>
              </div>
            )}

            {msg.status === 'error' && (
              <div style={{ color: '#f87171', padding: '6px 0', fontSize: 12 }}>
                处理失败: {msg.reply || '未知错误'}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: 6 }}>
              <span style={{ fontSize: 10, color: '#64748b' }}>
                {msg.processed_at ? fmtTime(msg.processed_at) : ''}
              </span>
              {msg.session_id && (
                <Tag color="geekblue" style={{ fontSize: 10, margin: 0, padding: '1px 6px' }}>
                  {msg.session_id.slice(0, 8)}
                </Tag>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}, (prev, next) => {
  // 只有 msg 引用变化或 events 长度变化才重渲染
  return prev.msg === next.msg &&
    prev.events === next.events &&
    prev.isProcessing === next.isProcessing
})

// ---- 主组件 ----

function ChatConsole() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [connected, setConnected] = useState(false)
  // 轻量级 tick: 每 500ms 触发一次重渲染 (而不是每个 token 触发)
  const [, setTick] = useState(0)

  // 事件存储: 全部用 ref, 绝不用 state
  const msgEventsRef = useRef<Map<string, AgentEvent[]>>(new Map())
  // agent_session_id -> msg_id
  const sessionToMsgRef = useRef<Map<string, string>>(new Map())
  const msgToSessionRef = useRef<Map<string, string>>(new Map())
  // processing 消息集合 (ref, 不触发渲染)
  const processingMsgsRef = useRef<Set<string>>(new Set())

  const scrollRef = useRef<HTMLDivElement>(null)
  const atBottomRef = useRef(true)
  const messagesRef = useRef<ChatMsg[]>([])
  const activeIdRef = useRef(activeId)

  // 同步 ref
  useEffect(() => { messagesRef.current = messages }, [messages])
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // ---- 500ms 定时刷新 (唯一触发渲染的源) ----
  useEffect(() => {
    const timer = setInterval(() => {
      if (processingMsgsRef.current.size > 0) {
        setTick(t => t + 1)
      }
    }, FLUSH_INTERVAL)
    return () => clearInterval(timer)
  }, [])

  // ---- 清理过期消息的事件 ----
  useEffect(() => {
    const cleanup = setInterval(() => {
      const validMsgIds = new Set(messagesRef.current.map(m => m.id))
      for (const msgId of Array.from(msgEventsRef.current.keys())) {
        if (!validMsgIds.has(msgId)) {
          msgEventsRef.current.delete(msgId)
          processingMsgsRef.current.delete(msgId)
        }
      }
    }, 30000) // 30秒清理一次
    return () => clearInterval(cleanup)
  }, [])

  // ---- fetch sessions（使用缓存）----
  const fetchSessions = useCallback(async () => {
    try {
      const data = await dataCache.fetch<ChatSession[]>(
        'chat_sessions',
        async () => {
          const res = await fetch('/api/chat/sessions')
          return res.json()
        },
        5000 // 5秒缓存
      )
      setSessions(data || []) // 防止 null
      if (data && data.length > 0 && !activeIdRef.current) {
        setActiveId(data[0].id)
      }
    } catch (e) {
      console.error('fetchSessions failed:', e)
      setSessions([]) // 设置空数组
    }
  }, [])

  useEffect(() => {
    fetchSessions()
    const t = setInterval(fetchSessions, 10000) // 10秒轮询（而不是5秒）
    return () => clearInterval(t)
  }, [fetchSessions])

  // ---- 切换 session 拉取消息 ----
  useEffect(() => {
    if (!activeId) { setMessages([]); return }
    fetch(`/api/chat/sessions/${activeId}/messages`)
      .then(res => res.json())
      .then((data: ChatMsg[]) => {
        setMessages(data)
        data.forEach(m => {
          if ((m.status === 'pending' || m.status === 'processing') && m.session_id) {
            sessionToMsgRef.current.set(m.session_id, m.id)
            msgToSessionRef.current.set(m.id, m.session_id)
            processingMsgsRef.current.add(m.id)
          }
        })
      })
      .catch(e => console.error('fetchMessages failed:', e))
  }, [activeId])

  // ---- 轮询 pending 消息获取 session_id ----
  useEffect(() => {
    const poll = setInterval(() => {
      const pending = messagesRef.current.filter(m =>
        (m.status === 'pending' || m.status === 'processing') && !m.session_id
      )
      if (pending.length === 0) return
      const sid = activeIdRef.current
      if (!sid) return
      fetch(`/api/chat/sessions/${sid}/messages`)
        .then(res => res.json())
        .then((data: ChatMsg[]) => {
          let changed = false
          for (const m of data) {
            if ((m.status === 'pending' || m.status === 'processing') && m.session_id) {
              if (!msgToSessionRef.current.get(m.id)) {
                sessionToMsgRef.current.set(m.session_id, m.id)
                msgToSessionRef.current.set(m.id, m.session_id)
                processingMsgsRef.current.add(m.id)
                changed = true
              }
            }
          }
          if (changed) setMessages(data)
        })
        .catch(() => {})
    }, 2000)
    return () => clearInterval(poll)
  }, [])

  // ---- WebSocket (使用全局单例) ----
  useEffect(() => {
    const unsubscribe = wsManager.subscribe((data) => {
      // 连接状态更新
      if (data.type === 'connection') {
        setConnected(data.status === 'connected')
        return
      }

      // agent_event 处理
      if (data.type !== 'agent_event') return

      const { session_id, kind, content } = data

      // 路由: session_id -> msg_id
      let msgId = sessionToMsgRef.current.get(session_id)

      if (!msgId) {
        const pending = messagesRef.current.find(m =>
          (m.status === 'pending' || m.status === 'processing') &&
          !msgToSessionRef.current.get(m.id)
        )
        if (pending) {
          msgId = pending.id
          sessionToMsgRef.current.set(session_id, msgId)
          msgToSessionRef.current.set(msgId, session_id)
          processingMsgsRef.current.add(msgId)
          // 标记 processing
          setMessages(prev => prev.map(m =>
            m.id === msgId ? { ...m, status: 'processing', session_id } : m
          ))
        } else {
          return
        }
      }

      // 追加事件到 ref (不触发渲染, 等 800ms tick)
      const current = msgEventsRef.current.get(msgId) || []

      if (kind === 'stream_reasoning' || kind === 'stream_content') {
        const last = current[current.length - 1]
        if (last && last.kind === kind) {
          const existingText = last.content?.text || ''
          const newText = content?.text || ''
          // 超长截断: 保留最新 60000 字符，防止单条流式事件无限膨胀
          const truncatedText = (existingText + newText).slice(-60000)
          // 直接修改对象，避免创建新数组
          last.content = { text: truncatedText }
        } else {
          current.push({ kind, content, ts: Date.now() / 1000, seq: current.length })
        }
      } else if (kind === 'reasoning' || kind === 'assistant') {
        const streamKind = kind === 'reasoning' ? 'stream_reasoning' : 'stream_content'
        const last = current[current.length - 1]
        if (last && last.kind === streamKind) {
          // 替换流式事件为最终事件，直接修改避免创建新对象
          last.kind = kind
          last.content = content
          last.seq = current.length - 1
        } else {
          current.push({ kind, content, ts: Date.now() / 1000, seq: current.length })
        }
      } else {
        // 普通事件：直接追加，不删除
        current.push({ kind, content, ts: Date.now() / 1000, seq: current.length })
      }

      // 保存事件
      msgEventsRef.current.set(msgId, current)

      // final_answer / error: 更新消息状态
      if (kind === 'final_answer' || kind === 'error') {
        setMessages(prev => prev.map(m => {
          if (m.id === msgId) {
            return {
              ...m,
              status: kind === 'final_answer' ? 'done' : 'error',
              reply: content?.text || '',
              processed_at: Math.floor(Date.now() / 1000)
            }
          }
          return m
        }))
        sessionToMsgRef.current.delete(session_id)
        msgToSessionRef.current.delete(msgId)
        processingMsgsRef.current.delete(msgId)
        fetchSessions()
        dataCache.invalidate('chat_sessions') // 清除缓存，强制下次刷新
      }
    })

    // 组件卸载时取消订阅
    return unsubscribe
  }, [fetchSessions])

  // ---- 自动滚动 (500ms tick 期间检查) ----
  useEffect(() => {
    if (atBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  })

  const handleScroll = () => {
    if (scrollRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
      atBottomRef.current = scrollHeight - scrollTop - clientHeight < 60
    }
  }

  // ---- 新建 / 删除 / 发送 ----
  const newSession = async () => {
    try {
      const res = await fetch('/api/chat/sessions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新对话' }),
      })
      const data: ChatSession = await res.json()
      setSessions(prev => [data, ...prev])
      setActiveId(data.id)
      setMessages([])
    } catch (e) { console.error('newSession failed:', e) }
  }

  const deleteSession = async (id: string) => {
    try {
      await fetch(`/api/chat/sessions/${id}`, { method: 'DELETE' })
      for (const m of messagesRef.current) {
        msgEventsRef.current.delete(m.id)
        processingMsgsRef.current.delete(m.id)
      }
      setSessions(prev => prev.filter(s => s.id !== id))
      if (activeId === id) {
        const remaining = sessions.filter(s => s.id !== id)
        setActiveId(remaining.length > 0 ? remaining[0].id : null)
      }
    } catch (e) { console.error('deleteSession failed:', e) }
  }

  const sendMessage = async () => {
    const msg = input.trim()
    if (!msg || !activeId || sending) return
    setSending(true)
    try {
      const res = await fetch(`/api/chat/sessions/${activeId}/messages`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }),
      })
      const data = await res.json()
      if (data.id) {
        setInput('')
        const newMsg: ChatMsg = {
          id: data.id, user_msg: msg, status: 'pending',
          session_id: '', reply: '', role: 'user',
          created_at: Math.floor(Date.now() / 1000), processed_at: 0,
        }
        // 立即更新 ref，避免 WebSocket 事件处理时找不到消息
        messagesRef.current = [...messagesRef.current, newMsg]
        setMessages(messagesRef.current)
        processingMsgsRef.current.add(data.id)
        msgEventsRef.current.delete(data.id)
      }
    } catch (e) { console.error('sendMessage failed:', e) }
    setSending(false)
  }

  const activeSession = sessions.find(s => s.id === activeId)
  const processingCount = processingMsgsRef.current.size

  return (
    <div style={{ display: 'flex', gap: 12, height: '100%', overflow: 'hidden', padding: '0 4px' }}>
      {/* 左侧: Session 列表 */}
      <Card
        size="small"
        style={{ width: 240, flexShrink: 0, height: '100%', display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'hidden', minHeight: 0, padding: '8px 0' } }}
        title={
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 13 }}>对话</span>
            <Tooltip title="新建对话">
              <Button size="small" type="text" icon={<PlusOutlined />} onClick={newSession} />
            </Tooltip>
          </div>
        }
      >
        <div className="chat-session-list" style={{ height: '100%', overflow: 'auto' }}>
          {sessions.length === 0 ? (
            <div style={{ padding: 16, textAlign: 'center' }}>
              <Empty description="无对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              <Button type="dashed" icon={<PlusOutlined />} onClick={newSession} size="small" style={{ marginTop: 8 }}>新建对话</Button>
            </div>
          ) : (
            <List
              dataSource={sessions}
              renderItem={(s) => (
                <List.Item
                  style={{
                    padding: '8px 12px', cursor: 'pointer',
                    background: s.id === activeId ? 'rgba(14,165,233,0.12)' : 'transparent',
                    borderLeft: s.id === activeId ? '3px solid #0ea5e9' : '3px solid transparent',
                    transition: 'all 0.2s',
                    marginBottom: 4,
                    borderRadius: '0 6px 6px 0',
                  }}
                  onClick={() => setActiveId(s.id)}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 13, fontWeight: s.id === activeId ? 600 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#e2e8f0',
                      marginBottom: 2,
                    }}>{s.title}</div>
                    <div style={{ fontSize: 10, color: '#64748b', marginTop: 2 }}>
                      {s.msg_count} 条 · {fmtTime(s.updated_at)}
                    </div>
                  </div>
                  <Popconfirm title="删除此对话?" onConfirm={(e) => { e?.stopPropagation(); deleteSession(s.id) }} onCancel={(e) => e?.stopPropagation()}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} style={{ opacity: 0.5 }} />
                  </Popconfirm>
                </List.Item>
              )}
            />
          )}
        </div>
      </Card>

      {/* 右侧: 聊天区域 */}
      <Card
        size="small"
        style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0 }}
        styles={{ body: { flex: 1, overflow: 'hidden', minHeight: 0, padding: 0, display: 'flex', flexDirection: 'column' } }}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <MessageOutlined style={{ color: '#38bdf8' }} />
            <span>{activeSession?.title || '对话'}</span>
            {processingCount > 0 && <Badge count={processingCount} status="processing" title="Agent 处理中" />}
            <Badge status={connected ? 'success' : 'error'} text={connected ? '实时' : '离线'} style={{ marginLeft: 'auto', fontSize: 11 }} />
          </div>
        }
      >
        <div ref={scrollRef} onScroll={handleScroll} className="chat-msg-list" style={{ flex: 1, overflow: 'auto', padding: '16px 20px' }}>
          {!activeId ? (
            <div style={{ textAlign: 'center', color: '#475569', marginTop: 60 }}>
              <RobotOutlined style={{ fontSize: 36, marginBottom: 10, color: '#334155' }} />
              <div style={{ fontSize: 13 }}>选择左侧对话或点击 <PlusOutlined /> 新建对话</div>
            </div>
          ) : messages.length === 0 ? (
            <div style={{ textAlign: 'center', color: '#475569', marginTop: 60 }}>
              <RobotOutlined style={{ fontSize: 36, marginBottom: 10, color: '#334155' }} />
              <div style={{ fontSize: 13 }}>向 Agent 提问或报障</div>
            </div>
          ) : (
            messages.map((msg) => {
              const events = msgEventsRef.current.get(msg.id) || []
              const isProcessing = processingMsgsRef.current.has(msg.id)
              return (
                <MessageItem key={msg.id} msg={msg} events={events} isProcessing={isProcessing} />
              )
            })
          )}
        </div>

        {/* 输入区 */}
        <div style={{ borderTop: '1px solid #1e293b', padding: '12px 16px', display: 'flex', gap: 8, alignItems: 'flex-end', flexShrink: 0, background: '#0a0f1a' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={activeId ? "提问或报障，如：HDFS /quota_test 配额超限" : "请先选择或新建对话"}
            autoSize={{ minRows: 1, maxRows: 4 }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }}
            style={{ flex: 1, resize: 'none', fontSize: 13, lineHeight: 1.6 }}
            disabled={sending || !activeId}
          />
          <Tooltip title="发送 (Enter 发送, Shift+Enter 换行)">
            <Button type="primary" icon={<SendOutlined />} onClick={sendMessage} loading={sending} disabled={!activeId} style={{ flexShrink: 0, height: 36 }}>发送</Button>
          </Tooltip>
        </div>
      </Card>
    </div>
  )
}

export default ChatConsole

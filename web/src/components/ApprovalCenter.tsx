import { useEffect, useState, useCallback } from 'react'

interface Approval {
  id: string
  session_id: string
  tool_name: string
  args: any
  risk_level: string
  dry_run: any
  status: string
  decided_by: string
  ts: number
}

function ApprovalCenter() {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [loading, setLoading] = useState(false)

  const fetchApprovals = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/approvals')
      const data = await res.json()
      setApprovals(data)
    } catch (e) {
      console.error('fetch approvals failed:', e)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchApprovals()
    const interval = setInterval(fetchApprovals, 5000)
    return () => clearInterval(interval)
  }, [fetchApprovals])

  const decide = async (id: string, status: string) => {
    await fetch(`/api/approvals/${id}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, decided_by: 'web-user' }),
    })
    fetchApprovals()
  }

  const pending = approvals.filter((a) => a.status === 'pending')
  const decided = approvals.filter((a) => a.status !== 'pending')

  const riskColor = (r: string) =>
    r === 'high' ? '#ef4444' : r === 'medium' ? '#f59e0b' : '#22c55e'

  const renderItem = (a: Approval) => (
    <div key={a.id} className="approval-item">
      <div className="approval-header">
        <span className="risk-badge" style={{ background: riskColor(a.risk_level) }}>
          {a.risk_level.toUpperCase()}
        </span>
        <span className="tool-name">{a.tool_name}</span>
        <span className="approval-status">{a.status}</span>
      </div>
      <div className="approval-args">
        <strong>Args:</strong> {JSON.stringify(a.args)}
      </div>
      {a.dry_run?.message && (
        <div className="approval-dryrun">
          <strong>Dry-run:</strong> {a.dry_run.message}
        </div>
      )}
      {a.status === 'pending' && (
        <div className="approval-actions">
          <button className="btn-approve" onClick={() => decide(a.id, 'approved')}>
            Approve
          </button>
          <button className="btn-reject" onClick={() => decide(a.id, 'rejected')}>
            Reject
          </button>
        </div>
      )}
      {a.decided_by && (
        <div className="approval-decided">
          by {a.decided_by}
        </div>
      )}
    </div>
  )

  return (
    <div className="approval-center">
      <div className="status-bar">
        <span>{loading ? 'Loading...' : 'Loaded'}</span>
        <span className="event-count">{pending.length} pending / {decided.length} decided</span>
        <button className="clear-btn" onClick={fetchApprovals}>Refresh</button>
      </div>
      {pending.length > 0 && (
        <>
          <h3>Pending ({pending.length})</h3>
          <div className="approval-list">
            {pending.map(renderItem)}
          </div>
        </>
      )}
      {decided.length > 0 && (
        <>
          <h3>History ({decided.length})</h3>
          <div className="approval-list">
            {decided.slice(0, 20).map(renderItem)}
          </div>
        </>
      )}
      {approvals.length === 0 && (
        <div className="empty">No approvals yet</div>
      )}
    </div>
  )
}

export default ApprovalCenter

import { useState } from 'react'
import AgentActivity from './components/AgentActivity'
import ApprovalCenter from './components/ApprovalCenter'
import './App.css'

type Tab = 'agent' | 'approval'

function App() {
  const [tab, setTab] = useState<Tab>('agent')

  return (
    <div className="app">
      <header className="header">
        <h1>AIOps Agent Console</h1>
        <nav className="tabs">
          <button
            className={tab === 'agent' ? 'tab active' : 'tab'}
            onClick={() => setTab('agent')}
          >
            Agent Activity
          </button>
          <button
            className={tab === 'approval' ? 'tab active' : 'tab'}
            onClick={() => setTab('approval')}
          >
            Approval Center
          </button>
        </nav>
      </header>
      <main className="content">
        {tab === 'agent' && <AgentActivity />}
        {tab === 'approval' && <ApprovalCenter />}
      </main>
    </div>
  )
}

export default App

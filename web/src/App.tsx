import { useState } from 'react'
import { ConfigProvider, theme, Layout, Tabs } from 'antd'
import AgentActivity from './components/AgentActivity'
import ApprovalCenter from './components/ApprovalCenter'

const { Header, Content } = Layout

function App() {
  const [tab, setTab] = useState('agent')

  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{ display: 'flex', alignItems: 'center' }}>
          <h2 style={{ color: '#38bdf8', margin: 0, marginRight: 32 }}>
            AIOps Agent Console
          </h2>
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: 'agent', label: 'Agent Activity' },
              { key: 'approval', label: 'Approval Center' },
            ]}
            style={{ flex: 1 }}
          />
        </Header>
        <Content style={{ padding: 24 }}>
          {tab === 'agent' && <AgentActivity />}
          {tab === 'approval' && <ApprovalCenter />}
        </Content>
      </Layout>
    </ConfigProvider>
  )
}

export default App

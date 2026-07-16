import { useState } from 'react'
import { ConfigProvider, theme, Layout, Menu } from 'antd'
import { RobotOutlined, SafetyOutlined } from '@ant-design/icons'
import AgentActivity from './components/AgentActivity'
import ApprovalCenter from './components/ApprovalCenter'
import './App.css'

const { Sider, Content } = Layout

function App() {
  const [tab, setTab] = useState('agent')

  const menuItems = [
    { key: 'agent', icon: <RobotOutlined />, label: 'Agent 活动台' },
    { key: 'approval', icon: <SafetyOutlined />, label: '审批中心' },
  ]

  return (
    <ConfigProvider theme={{
      algorithm: theme.darkAlgorithm,
      token: { colorPrimary: '#0ea5e9' },
    }}>
      <Layout style={{ height: '100vh', overflow: 'hidden' }}>
        <Sider width={200} theme="dark" style={{ overflow: 'hidden' }}>
          <div style={{
            height: 56, display: 'flex', alignItems: 'center',
            justifyContent: 'center', color: '#38bdf8',
            fontWeight: 700, fontSize: 15, borderBottom: '1px solid #1e293b',
          }}>
            AIOps 控制台
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[tab]}
            items={menuItems}
            onClick={(e) => setTab(e.key)}
          />
        </Sider>
        <Layout style={{ overflow: 'hidden', height: '100%' }}>
          <Content style={{ height: '100%', overflow: 'hidden', padding: 16 }}>
            {tab === 'agent' && <AgentActivity />}
            {tab === 'approval' && <ApprovalCenter />}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default App

import { useState, useEffect } from 'react'
import {
  ConfigProvider, theme, Layout, Menu, Avatar, Badge, Switch,
  Breadcrumb, Card, Form, Input, Button, Space, Typography,
} from 'antd'
import {
  RobotOutlined, SafetyOutlined, BellOutlined, UserOutlined,
  BulbFilled, BulbOutlined, LogoutOutlined, BookOutlined,
} from '@ant-design/icons'
import AgentActivity from './components/AgentActivity'
import ApprovalCenter from './components/ApprovalCenter'
import RiskRules from './components/RiskRules'
import KnowledgeBase from './components/KnowledgeBase'
import './App.css'

const { Sider, Header, Content } = Layout
const { Title } = Typography

function LoginPage({ onLogin }: { onLogin: (name: string) => void }) {
  const [loading, setLoading] = useState(false)
  return (
    <div style={{
      display: 'flex', justifyContent: 'center', alignItems: 'center',
      height: '100vh', background: '#0f172a',
    }}>
      <Card style={{ width: 380 }}>
        <Title level={3} style={{ textAlign: 'center', color: '#38bdf8' }}>
          AIOps 控制台
        </Title>
        <Form
          onFinish={(v: any) => {
            setLoading(true)
            setTimeout(() => { onLogin(v.username || 'admin'); setLoading(false) }, 300)
          }}
        >
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<SafetyOutlined />} placeholder="密码" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}

function App() {
  const [logged, setLogged] = useState(() => !!localStorage.getItem('aiops_user'))
  const [user, setUser] = useState(() => localStorage.getItem('aiops_user') || 'admin')
  const [collapsed, setCollapsed] = useState(false)
  const [darkMode, setDarkMode] = useState(true)
  const [tab, setTab] = useState('agent')
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    const fetchPending = async () => {
      try {
        const res = await fetch('/api/approvals?status=pending')
        const data = await res.json()
        setPendingCount(Array.isArray(data) ? data.length : 0)
      } catch (e) {
        console.error('fetch pending approvals failed:', e)
      }
    }
    fetchPending()
    const t = setInterval(fetchPending, 5000)
    return () => clearInterval(t)
  }, [])

  if (!logged) {
    return (
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
        <LoginPage onLogin={(name) => {
          localStorage.setItem('aiops_user', name)
          setUser(name); setLogged(true)
        }} />
      </ConfigProvider>
    )
  }

  const menuItems = [
    { key: 'agent', icon: <RobotOutlined />, label: 'Agent 活动台' },
    { key: 'approval', icon: <SafetyOutlined />, label: '审批中心' },
    { key: 'rules', icon: <BulbOutlined />, label: '风险规则' },
    { key: 'kb', icon: <BookOutlined />, label: '知识库' },
  ]
  const currentLabel = menuItems.find(m => m.key === tab)?.label || ''

  return (
    <ConfigProvider theme={{
      algorithm: darkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
      token: { colorPrimary: '#0ea5e9' },
    }}>
      <Layout style={{ height: '100vh', overflow: 'hidden' }}>
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          theme={darkMode ? 'dark' : 'light'}
          width={200}
        >
          <div style={{
            height: 56, display: 'flex', alignItems: 'center',
            justifyContent: 'center', color: '#38bdf8',
            fontWeight: 700, fontSize: collapsed ? 14 : 15,
            borderBottom: '1px solid rgba(255,255,255,0.06)',
          }}>
            {collapsed ? 'AIOps' : 'AIOps 控制台'}
          </div>
          <Menu
            theme={darkMode ? 'dark' : 'light'}
            mode="inline"
            selectedKeys={[tab]}
            items={menuItems}
            onClick={(e) => setTab(e.key)}
          />
        </Sider>

        {/* 内层 Layout 用 flex column, Header/面包屑固定高度, Content flex:1 填满 */}
        <Layout style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', height: '100%' }}>
          <Header style={{
            display: 'flex', alignItems: 'center',
            justifyContent: 'flex-end', padding: '0 24px', flexShrink: 0,
          }}>
            <Space size="large">
              <Badge count={pendingCount} size="small">
                <BellOutlined style={{ fontSize: 18 }} />
              </Badge>
              <Switch
                checked={darkMode}
                onChange={setDarkMode}
                checkedChildren={<BulbFilled />}
                unCheckedChildren={<BulbOutlined />}
              />
              <Space>
                <Avatar size="small" icon={<UserOutlined />} />
                <span style={{ fontSize: 14 }}>{user}</span>
              </Space>
              <Button
                type="text" size="small"
                icon={<LogoutOutlined />}
                onClick={() => {
                  localStorage.removeItem('aiops_user')
                  setLogged(false)
                }}
              />
            </Space>
          </Header>

          <div style={{ padding: '8px 24px', flexShrink: 0 }}>
            <Breadcrumb items={[{ title: '首页' }, { title: currentLabel }]} />
          </div>

          {/* Content 用 flex:1 填满剩余空间, position:relative 让子元素 absolute 定位 */}
          <Content style={{
            flex: 1, overflow: 'hidden', padding: 16,
            position: 'relative', minHeight: 0,
          }}>
            {/* 用 display 切换而非条件渲染, 避免 AgentActivity 卸载丢失 state */}
            <div style={{
              position: 'absolute', inset: 0,
              display: tab === 'agent' ? 'flex' : 'none',
            }}>
              <AgentActivity />
            </div>
            <div style={{
              position: 'absolute', inset: 0,
              display: tab === 'approval' ? 'block' : 'none',
            }}>
              <ApprovalCenter />
            </div>
            <div style={{
              position: 'absolute', inset: 0,
              display: tab === 'rules' ? 'block' : 'none',
            }}>
              <RiskRules />
            </div>
            <div style={{
              position: 'absolute', inset: 0,
              display: tab === 'kb' ? 'block' : 'none',
            }}>
              <KnowledgeBase />
            </div>
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default App

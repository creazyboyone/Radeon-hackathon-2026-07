import { useState, useEffect } from 'react'
import {
  ConfigProvider, theme, Layout, Menu, Avatar, Badge, Switch,
  Breadcrumb, Card, Form, Input, Button, Space, Typography,
} from 'antd'
import {
  RobotOutlined, SafetyOutlined, BellOutlined, UserOutlined,
  BulbFilled, BulbOutlined, LogoutOutlined,
} from '@ant-design/icons'
import AgentActivity from './components/AgentActivity'
import ApprovalCenter from './components/ApprovalCenter'
import './App.css'

const { Sider, Header, Content } = Layout
const { Title } = Typography

// ---- 登录页 ----
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

  if (!logged) {
    return (
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
        <LoginPage onLogin={(name) => {
          localStorage.setItem('aiops_user', name)
          setUser(name)
          setLogged(true)
        }} />
      </ConfigProvider>
    )
  }

  const menuItems = [
    { key: 'agent', icon: <RobotOutlined />, label: 'Agent 活动台' },
    { key: 'approval', icon: <SafetyOutlined />, label: '审批中心' },
  ]
  const currentLabel = menuItems.find(m => m.key === tab)?.label || ''
  const breadcrumbItems = [
    { title: '首页' },
    { title: currentLabel },
  ]

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

        <Layout style={{ overflow: 'hidden' }}>
          {/* Header: 右侧用户信息/通知/主题切换 */}
          <Header style={{
            display: 'flex', alignItems: 'center',
            justifyContent: 'flex-end', padding: '0 24px',
          }}>
            <Space size="large">
              <Badge count={0} size="small">
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

          {/* 面包屑 */}
          <div style={{ padding: '8px 24px' }}>
            <Breadcrumb items={breadcrumbItems} />
          </div>

          {/* 内容区 */}
          <Content style={{ padding: 16, height: '100%', overflow: 'hidden', flex: 1 }}>
            {tab === 'agent' && <AgentActivity />}
            {tab === 'approval' && <ApprovalCenter />}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default App

import { useState } from 'react'
import { App, Button, Dropdown, Empty, Flex, Input, Menu, Modal, Skeleton, Typography, theme } from 'antd'
import { DeleteOutlined, EditOutlined, EllipsisOutlined, PlusOutlined } from '@ant-design/icons'
import { groupChats, useChats } from '../lib/chats'
import type { ChatInfo } from '../lib/api'

/** The signed-in user's chats in the sidebar: New chat, then chats grouped by last activity.
 *  Each has a menu to rename or delete it. */
export function ChatList({ current, onOpen }: { current: string | null; onOpen: (id: string | null) => void }) {
  const { token } = theme.useToken()
  const { message, modal } = App.useApp()
  const { chats, loaded, rename, remove } = useChats()
  const [editing, setEditing] = useState<ChatInfo | null>(null)
  const [title, setTitle] = useState('')
  const [saving, setSaving] = useState(false)

  const startRename = (c: ChatInfo) => { setEditing(c); setTitle(c.title) }
  const saveRename = async () => {
    if (!editing || !title.trim()) return
    setSaving(true)
    try {
      await rename(editing.id, title.trim())
      setEditing(null)
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Could not rename the chat')
    } finally { setSaving(false) }
  }
  const confirmDelete = (c: ChatInfo) => {
    modal.confirm({
      title: 'Delete this chat?',
      content: `“${c.title}” and its ${c.turns} answer${c.turns === 1 ? '' : 's'} will be deleted.`,
      okText: 'Delete', okButtonProps: { danger: true }, cancelText: 'Cancel',
      onOk: async () => {
        try {
          await remove(c.id)
          if (c.id === current) onOpen(null)
        } catch (e) {
          message.error(e instanceof Error ? e.message : 'Could not delete the chat')
        }
      },
    })
  }

  const groups = groupChats(chats)
  return (
    <Flex vertical style={{ flex: 1, minHeight: 0 }}>
      <div style={{ padding: `${token.paddingXS}px ${token.padding}px` }}>
        <Button block icon={<PlusOutlined />} onClick={() => onOpen(null)}>New chat</Button>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {!loaded ? (
          <div style={{ padding: token.padding }}><Skeleton active title={false} paragraph={{ rows: 4 }} /></div>
        ) : !chats.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No chats yet" />
        ) : (
          <Menu
            mode="inline"
            selectedKeys={current ? [current] : []}
            onClick={(e) => onOpen(e.key)}
            style={{ borderInlineEnd: 0 }}
            items={groups.map((g) => ({
              type: 'group' as const,
              key: g.label,
              label: g.label,
              children: g.chats.map((c) => ({
                key: c.id,
                title: c.title,
                label: <Typography.Text ellipsis style={{ maxWidth: '100%', color: 'inherit' }}>{c.title}</Typography.Text>,
                extra: (
                  <Dropdown
                    trigger={['click']}
                    menu={{
                      items: [
                        { key: 'rename', icon: <EditOutlined />, label: 'Rename', onClick: ({ domEvent }) => { domEvent.stopPropagation(); startRename(c) } },
                        { key: 'delete', icon: <DeleteOutlined />, label: 'Delete', danger: true, onClick: ({ domEvent }) => { domEvent.stopPropagation(); confirmDelete(c) } },
                      ],
                    }}
                  >
                    <Button
                      type="text" size="small" icon={<EllipsisOutlined />} aria-label={`Options for ${c.title}`}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Dropdown>
                ),
              })),
            }))}
          />
        )}
      </div>
      <Modal
        title="Rename chat"
        open={!!editing}
        okText="Save"
        confirmLoading={saving}
        okButtonProps={{ disabled: !title.trim() }}
        onOk={saveRename}
        onCancel={() => setEditing(null)}
        destroyOnHidden
      >
        <Input
          autoFocus value={title} maxLength={60} showCount onChange={(e) => setTitle(e.target.value)}
          onPressEnter={saveRename} aria-label="Chat name"
        />
      </Modal>
    </Flex>
  )
}

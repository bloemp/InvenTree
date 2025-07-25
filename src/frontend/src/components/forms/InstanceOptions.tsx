import { t } from '@lingui/core/macro';
import { Trans } from '@lingui/react/macro';
import {
  ActionIcon,
  Divider,
  Group,
  Select,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { useToggle } from '@mantine/hooks';
import {
  IconApi,
  IconCircleCheck,
  IconEdit,
  IconInfoCircle,
  IconPlugConnected,
  IconServer,
  IconServerSpark
} from '@tabler/icons-react';

import { ActionButton } from '@lib/components/ActionButton';
import type { HostList } from '@lib/types/Server';
import { useShallow } from 'zustand/react/shallow';
import { Wrapper } from '../../pages/Auth/Layout';
import { useLocalState } from '../../states/LocalState';
import { useServerApiState } from '../../states/ServerApiState';
import { HostOptionsForm } from './HostOptionsForm';

export function InstanceOptions({
  hostKey,
  ChangeHost,
  setHostEdit
}: Readonly<{
  hostKey: string;
  ChangeHost: (newHost: string | null) => void;
  setHostEdit: () => void;
}>) {
  const [hostListEdit, setHostListEdit] = useToggle([false, true] as const);
  const [setHost, setHostList, hostList] = useLocalState(
    useShallow((state) => [state.setHost, state.setHostList, state.hostList])
  );
  const hostListData = Object.keys(hostList).map((key) => ({
    value: key,
    label: hostList[key]?.name
  }));

  function SaveOptions(newHostList: HostList): void {
    setHostList(newHostList);
    if (newHostList[hostKey] === undefined) {
      setHost('', '');
    }
    setHostListEdit();
  }

  return (
    <Wrapper titleText={t`Select Server`} smallPadding>
      <Group gap='xs' justify='space-between' wrap='nowrap'>
        <Select
          style={{ width: '100%' }}
          value={hostKey}
          onChange={ChangeHost}
          data={hostListData}
          disabled={hostListEdit}
        />
        <Group gap='xs' wrap='nowrap'>
          <Tooltip label={t`Edit host options`} position='top'>
            <ActionButton
              variant='transparent'
              disabled={hostListEdit}
              onClick={setHostListEdit}
              icon={<IconEdit />}
            />
          </Tooltip>
          <Tooltip label={t`Save host selection`} position='top'>
            <ActionButton
              variant='transparent'
              onClick={setHostEdit}
              disabled={hostListEdit}
              icon={<IconCircleCheck />}
              color='green'
            />
          </Tooltip>
        </Group>
      </Group>

      {hostListEdit ? (
        <>
          <Divider my={'sm'} />
          <Text>
            <Trans>Edit host options</Trans>
          </Text>
          <HostOptionsForm data={hostList} saveOptions={SaveOptions} />
        </>
      ) : (
        <>
          <Divider my={'sm'} />
          <ServerInfo hostList={hostList} hostKey={hostKey} />
        </>
      )}
    </Wrapper>
  );
}

function ServerInfo({
  hostList,
  hostKey
}: Readonly<{
  hostList: HostList;
  hostKey: string;
}>) {
  const [server] = useServerApiState(useShallow((state) => [state.server]));

  const items: any[] = [
    {
      key: 'server',
      label: t`Server`,
      value: hostList[hostKey]?.host,
      icon: <IconServer />
    },
    {
      key: 'name',
      label: t`Name`,
      value: server.instance,
      icon: <IconInfoCircle />
    },
    {
      key: 'version',
      label: t`Version`,
      value: server.version,
      icon: <IconInfoCircle />
    },
    {
      key: 'api',
      label: t`API Version`,
      value: server.apiVersion,
      icon: <IconApi />
    },
    {
      key: 'plugins',
      label: t`Plugins`,
      value: server.plugins_enabled ? t`Enabled` : t`Disabled`,
      icon: <IconPlugConnected />,
      color: server.plugins_enabled ? 'green' : 'red'
    },
    {
      key: 'worker',
      label: t`Worker`,
      value: server.worker_running ? t`Running` : t`Stopped`,
      icon: <IconServerSpark />,
      color: server.worker_running ? 'green' : 'red'
    }
  ];

  return (
    <Table striped p='xs'>
      <Table.Tbody>
        {items.map((item) => (
          <Table.Tr key={item.key} p={2}>
            <Table.Td>
              <ActionIcon size='xs' variant='transparent' color={item.color}>
                {item.icon}
              </ActionIcon>
            </Table.Td>
            <Table.Td>
              <Text size='sm'>{item.label}</Text>
            </Table.Td>
            <Table.Td>
              <Text size='sm'>{item.value}</Text>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

import { t } from '@lingui/macro';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '../App';
import { ApiEndpoints } from '../enums/ApiEndpoints';
import { apiUrl } from '../states/ApiState';
import { StatisticItem } from './items/DashboardItem';
import { ErrorItem } from './items/ErrorItem';

export function DashboardItemProxy({
  id,
  text,
  url,
  params,
  autoupdate = true
}: Readonly<{
  id: string;
  text: string;
  url: ApiEndpoints;
  params: any;
  autoupdate: boolean;
}>) {
  function fetchData() {
    return api
      .get(`${apiUrl(url)}?search=&offset=0&limit=25`, { params: params })
      .then((res) => res.data);
  }
  const { isLoading, error, data, isFetching } = useQuery({
    queryKey: [`dash_${id}`],
    queryFn: fetchData,
    refetchOnWindowFocus: autoupdate
  });
  const [dashData, setDashData] = useState({ title: t`Title`, value: '000' });

  useEffect(() => {
    if (data) {
      setDashData({ title: text, value: data.count });
    }
  }, [data]);

  if (error != null) return <ErrorItem id={id} error={error} />;
  return (
    <div key={id}>
      <StatisticItem
        id={id}
        data={dashData}
        isLoading={isLoading || isFetching}
      />
    </div>
  );
}

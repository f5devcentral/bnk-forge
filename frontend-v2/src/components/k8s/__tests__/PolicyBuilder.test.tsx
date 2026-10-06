import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { PolicyBuilder } from '../PolicyBuilder';

const bnkData = {
  topology: [{
    name: 'gw-1', namespace: 'default', gatewayClassName: 'f5-gateway-class', addresses: [],
    accepted: true, programmed: true, conditions: [], listeners: [],
  }],
  palette: { addressLists: [], portLists: [], iRules: [{ name: 'ir-1', namespace: 'default', lineCount: 3, eventHandlers: [] }] },
  policyAssociations: [],
  policyCount: 0,
};

describe('PolicyBuilder', () => {
  it('refetches BNK data past the backend cache after apply', async () => {
    const dataRequests: URL[] = [];
    const created: string[] = [];
    server.use(
      http.get('*/api/k8s/clusters/:id/f5bnk/data', ({ request }) => {
        dataRequests.push(new URL(request.url));
        return HttpResponse.json(bnkData);
      }),
      http.post('*/api/k8s/clusters/:id/resources/:type', ({ params }) => {
        created.push(String(params.type));
        return HttpResponse.json({ success: true, message: 'created', resource: {} });
      }),
    );

    render(<PolicyBuilder clusterId={1} />);
    fireEvent.click(await screen.findByText('Network'));
    fireEvent.change(screen.getByPlaceholderText('my-policy'), { target: { value: 'net-1' } });
    fireEvent.click(screen.getByText('All Listeners'));
    fireEvent.click(screen.getByText('ir-1'));
    fireEvent.click(screen.getByRole('button', { name: /Apply/ }));

    await waitFor(() => expect(created.length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(dataRequests.slice(1).some((u) => u.searchParams.get('force') === 'true')).toBe(true),
    );
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpApiError } from '../api/client';
import {
  describe as describeError,
  installErrorReporting,
  isAbort,
  report,
  resetErrorReporting,
  scrubEndpoint,
  type ErrorReport,
} from './error-reporting';

afterEach(() => resetErrorReporting());

function httpError(overrides: Partial<ConstructorParameters<typeof HttpApiError>[0]> = {}) {
  return new HttpApiError({
    status: 409,
    message: '训练记录已被其他页面修改',
    serverCode: 'conflict',
    clientCorrelationId: 'client-abc',
    retryable: false,
    endpoint: '/data/training-records/rec-1',
    ...overrides,
  });
}

describe('app/error-reporting', () => {
  it('recognises DOMException aborts', () => {
    expect(isAbort(new DOMException('cancelled', 'AbortError'))).toBe(true);
  });

  it('does not treat a timeout as an abort', () => {
    expect(isAbort(new DOMException('timed out', 'TimeoutError'))).toBe(false);
  });

  it('strips query strings and hashes from endpoints', () => {
    expect(scrubEndpoint('/health/trend?metric=hrv&end_date=2026-09-11')).toBe('/health/trend');
    expect(scrubEndpoint('/data/overview#section')).toBe('/data/overview');
    expect(scrubEndpoint(undefined)).toBeUndefined();
  });

  it('reports only module, endpoint, status, serverCode and correlation id', () => {
    const described = describeError(httpError(), 'workout') as ErrorReport;
    expect(described).toEqual({
      module: 'workout',
      endpoint: '/data/training-records/rec-1',
      status: 409,
      serverCode: 'conflict',
      clientCorrelationId: 'client-abc',
      kind: 'HttpApiError',
    });
  });

  it('never carries the message, stack or response body into the report', () => {
    const described = describeError(
      httpError({ details: { records: [{ weight_kg: 88.5 }] } }),
      'health',
    ) as ErrorReport;
    const serialised = JSON.stringify(described);
    expect(serialised).not.toContain('训练记录已被其他页面修改');
    expect(serialised).not.toContain('weight_kg');
    expect(described).not.toHaveProperty('details');
    expect(described).not.toHaveProperty('stack');
    expect(described).not.toHaveProperty('message');
  });

  it('scrubs the query string of an endpoint captured by the client', () => {
    const described = describeError(
      httpError({ endpoint: '/health/overview?day=2026-09-11' }),
      'health',
    ) as ErrorReport;
    expect(described.endpoint).toBe('/health/overview');
  });

  it('drops aborts entirely so cancellation is not a failure', () => {
    expect(describeError(new DOMException('cancelled', 'AbortError'), 'session')).toBeNull();
  });

  it('falls back to the error name for non-HTTP failures', () => {
    expect(describeError(new TypeError('bad'), 'chat')).toEqual({
      module: 'chat',
      kind: 'TypeError',
    });
  });

  it('defaults the module to unknown', () => {
    expect(describeError(new Error('x'))?.module).toBe('unknown');
  });

  it('routes reports to the injected sink', () => {
    const sink = vi.fn();
    installErrorReporting(sink);
    report(httpError(), 'uploads');
    expect(sink).toHaveBeenCalledTimes(1);
    expect(sink.mock.calls[0][0].module).toBe('uploads');
  });

  it('an aborted rejection is swallowed, not reported', () => {
    const sink = vi.fn();
    installErrorReporting(sink);
    report(new DOMException('cancelled', 'AbortError'), 'session');
    expect(sink).not.toHaveBeenCalled();
  });

  it('installing twice does not register duplicate listeners', () => {
    const sink = vi.fn();
    const addEventListener = vi.spyOn(window, 'addEventListener');
    installErrorReporting(sink);
    const afterFirst = addEventListener.mock.calls.length;
    installErrorReporting(sink);
    expect(addEventListener.mock.calls.length).toBe(afterFirst);
    addEventListener.mockRestore();
  });
});

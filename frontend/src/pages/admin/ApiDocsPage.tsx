import { useState, useEffect, useMemo } from 'react';
import {
  DocumentTextIcon,
  MagnifyingGlassIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClipboardIcon,
  CheckIcon,
  ArrowPathIcon,
  LockClosedIcon,
  GlobeAltIcon,
  ArrowTopRightOnSquareIcon,
} from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
interface Parameter {
  name: string;
  in: string;
  required?: boolean;
  schema?: { type?: string; format?: string; default?: unknown; enum?: string[] };
  description?: string;
}

interface RequestBody {
  required?: boolean;
  content?: Record<string, { schema?: Record<string, unknown> }>;
}

interface Response {
  description?: string;
  content?: Record<string, { schema?: Record<string, unknown> }>;
}

interface Endpoint {
  method: string;
  path: string;
  summary?: string;
  description?: string;
  tags?: string[];
  operationId?: string;
  parameters?: Parameter[];
  requestBody?: RequestBody;
  responses?: Record<string, Response>;
  security?: Record<string, string[]>[];
}

interface OpenAPISpec {
  info: { title: string; version: string; description?: string };
  paths: Record<string, Record<string, Record<string, unknown>>>;
  components?: { schemas?: Record<string, Record<string, unknown>> };
}

const methodColors: Record<string, { bg: string; text: string; border: string }> = {
  get:    { bg: 'bg-blue-50 dark:bg-blue-900/20',   text: 'text-blue-700 dark:text-blue-400',   border: 'border-blue-200 dark:border-blue-800' },
  post:   { bg: 'bg-green-50 dark:bg-green-900/20',  text: 'text-green-700 dark:text-green-400',  border: 'border-green-200 dark:border-green-800' },
  put:    { bg: 'bg-orange-50 dark:bg-orange-900/20', text: 'text-orange-700 dark:text-orange-400', border: 'border-orange-200 dark:border-orange-800' },
  patch:  { bg: 'bg-yellow-50 dark:bg-yellow-900/20', text: 'text-yellow-700 dark:text-yellow-400', border: 'border-yellow-200 dark:border-yellow-800' },
  delete: { bg: 'bg-red-50 dark:bg-red-900/20',     text: 'text-red-700 dark:text-red-400',     border: 'border-red-200 dark:border-red-800' },
};

const methodBadgeColors: Record<string, string> = {
  get:    'bg-blue-600',
  post:   'bg-green-600',
  put:    'bg-orange-600',
  patch:  'bg-yellow-600',
  delete: 'bg-red-600',
};

export function ApiDocsPage() {
  const [spec, setSpec] = useState<OpenAPISpec | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  const [expandedEndpoints, setExpandedEndpoints] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);
  useEffect(() => {
    loadSpec();
  }, []);

  const loadSpec = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const { getAccessToken } = await import('@/services/tokenMemory');
      const token = getAccessToken();
      const headers: HeadersInit = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const resp = await fetch('/api/openapi.json', { headers, credentials: 'include' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSpec(data);
      // Expand all tags by default
      const tags = new Set<string>();
      Object.values(data.paths || {}).forEach((methods) => {
        Object.values(methods as Record<string, unknown>).forEach((op) => {
          const operation = op as Record<string, unknown>;
          ((operation.tags as string[]) || ['Other']).forEach((t: string) => tags.add(t));
        });
      });
      setExpandedTags(tags);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load API spec');
    } finally {
      setIsLoading(false);
    }
  };

  // Parse endpoints grouped by tag
  const { groupedEndpoints, allEndpoints } = useMemo(() => {
    if (!spec) return { groupedEndpoints: new Map<string, Endpoint[]>(), allEndpoints: [] as Endpoint[] };

    const grouped = new Map<string, Endpoint[]>();
    const all: Endpoint[] = [];

    Object.entries(spec.paths).forEach(([path, methods]) => {
      Object.entries(methods).forEach(([method, operation]) => {
        if (['get', 'post', 'put', 'patch', 'delete'].includes(method)) {
          const op = operation as Record<string, unknown>;
          const endpoint: Endpoint = {
            method,
            path,
            summary: op.summary as string,
            description: op.description as string,
            tags: (op.tags as string[]) || ['Other'],
            operationId: op.operationId as string,
            parameters: op.parameters as Parameter[],
            requestBody: op.requestBody as RequestBody,
            responses: op.responses as Record<string, Response>,
            security: op.security as Record<string, string[]>[],
          };
          all.push(endpoint);

          endpoint.tags!.forEach((tag) => {
            if (!grouped.has(tag)) grouped.set(tag, []);
            grouped.get(tag)!.push(endpoint);
          });
        }
      });
    });

    return { groupedEndpoints: grouped, allEndpoints: all };
  }, [spec]);

  // Filter endpoints by search
  const filteredGroups = useMemo(() => {
    if (!search.trim()) return groupedEndpoints;

    const q = search.toLowerCase();
    const filtered = new Map<string, Endpoint[]>();

    groupedEndpoints.forEach((endpoints, tag) => {
      const matching = endpoints.filter(
        (ep) =>
          ep.path.toLowerCase().includes(q) ||
          ep.summary?.toLowerCase().includes(q) ||
          ep.method.toLowerCase().includes(q) ||
          ep.operationId?.toLowerCase().includes(q) ||
          tag.toLowerCase().includes(q)
      );
      if (matching.length > 0) filtered.set(tag, matching);
    });

    return filtered;
  }, [groupedEndpoints, search]);

  const toggleTag = (tag: string) => {
    setExpandedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const toggleEndpoint = (id: string) => {
    setExpandedEndpoints((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const copyToClipboard = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch {
      toast.error('Copy failed');
    }
  };

  const generateCurl = (ep: Endpoint) => {
    const baseUrl = window.location.origin;
    let curl = `curl -X ${ep.method.toUpperCase()} '${baseUrl}${ep.path}'`;
    if (ep.security && ep.security.length > 0) {
      curl += ` \\\n  -H 'Authorization: Bearer <YOUR_TOKEN_OR_USE_SESSION_COOKIE>'`;
    }
    if (ep.requestBody) {
      curl += ` \\\n  -H 'Content-Type: application/json'`;
      curl += ` \\\n  -d '{}'`;
    }
    return curl;
  };

  const resolveSchemaRef = (ref: string): Record<string, unknown> | null => {
    if (!spec?.components?.schemas || !ref.startsWith('#/components/schemas/')) return null;
    const name = ref.replace('#/components/schemas/', '');
    return spec.components.schemas[name] || null;
  };

  const renderSchema = (schema: Record<string, unknown>, depth = 0): string => {
    if (schema.$ref) {
      const resolved = resolveSchemaRef(schema.$ref as string);
      if (resolved) return renderSchema(resolved, depth);
      return (schema.$ref as string).split('/').pop() || 'object';
    }
    if (schema.type === 'array' && schema.items) {
      return `Array<${renderSchema(schema.items as Record<string, unknown>, depth)}>`;
    }
    if (schema.type === 'object' || schema.properties) {
      const props = schema.properties as Record<string, Record<string, unknown>> | undefined;
      if (!props) return 'object';
      const required = (schema.required as string[]) || [];
      const lines = Object.entries(props).map(([key, val]) => {
        const isRequired = required.includes(key);
        const type = val.$ref
          ? ((val.$ref as string).split('/').pop() || 'object')
          : (val.type as string || 'any');
        return `  ${key}${isRequired ? '' : '?'}: ${type}`;
      });
      if (lines.length > 8) {
        return `{ ${lines.slice(0, 8).join(', ')}, ... (${lines.length} fields) }`;
      }
      return `{ ${lines.join(', ')} }`;
    }
    return (schema.type as string) || 'any';
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary-500 mb-3" />
        <p className="text-gray-500 dark:text-gray-400">Loading API specification...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <DocumentTextIcon className="h-12 w-12 text-red-400 mb-3" />
        <p className="text-red-500 font-medium mb-2">Failed to load API docs</p>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">{error}</p>
        <button onClick={loadSpec} className="btn-primary text-sm">Retry</button>
      </div>
    );
  }

  if (!spec) return null;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-3">
              <DocumentTextIcon className="h-7 w-7 text-primary-500" />
              API Documentation
            </h1>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              {spec.info.title} v{spec.info.version} — {allEndpoints.length} endpoints
            </p>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="/api/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-primary-600 dark:text-primary-400 hover:text-primary-700 dark:hover:text-primary-300 transition-colors"
            >
              Swagger UI
              <ArrowTopRightOnSquareIcon className="h-4 w-4" />
            </a>
            <span className="text-gray-300 dark:text-gray-600">|</span>
            <a
              href="/api/redoc"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-primary-600 dark:text-primary-400 hover:text-primary-700 dark:hover:text-primary-300 transition-colors"
            >
              ReDoc
              <ArrowTopRightOnSquareIcon className="h-4 w-4" />
            </a>
          </div>
        </div>

        {/* Stats bar */}
        <div className="flex items-center gap-4 mt-4">
          {['get', 'post', 'patch', 'put', 'delete'].map((method) => {
            const count = allEndpoints.filter((ep) => ep.method === method).length;
            if (count === 0) return null;
            return (
              <div key={method} className="flex items-center gap-1.5">
                <span className={`inline-block w-2.5 h-2.5 rounded-sm ${methodBadgeColors[method]}`} />
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  {method.toUpperCase()}: {count}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-6">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search endpoints by path, name, method..."
          className="input-field pl-10 w-full"
        />
        {search && (
          <button
            onClick={() => setSearch('')}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            &times;
          </button>
        )}
      </div>

      {/* Quick actions */}
      <div className="flex items-center gap-2 mb-6">
        <button
          onClick={() => {
            const allTags = new Set<string>();
            groupedEndpoints.forEach((_, tag) => allTags.add(tag));
            setExpandedTags(allTags);
          }}
          className="text-xs text-primary-600 dark:text-primary-400 hover:underline"
        >
          Expand all
        </button>
        <span className="text-gray-300 dark:text-gray-600">|</span>
        <button
          onClick={() => { setExpandedTags(new Set()); setExpandedEndpoints(new Set()); }}
          className="text-xs text-primary-600 dark:text-primary-400 hover:underline"
        >
          Collapse all
        </button>
        <span className="text-gray-300 dark:text-gray-600">|</span>
        <button
          onClick={() => copyToClipboard(`${window.location.origin}/api/openapi.json`, 'spec-url')}
          className="text-xs text-primary-600 dark:text-primary-400 hover:underline inline-flex items-center gap-1"
        >
          {copiedId === 'spec-url' ? <CheckIcon className="h-3 w-3" /> : <ClipboardIcon className="h-3 w-3" />}
          Copy OpenAPI URL
        </button>
      </div>

      {/* Endpoint groups */}
      <div className="space-y-4">
        {filteredGroups.size === 0 && (
          <div className="text-center py-12">
            <MagnifyingGlassIcon className="h-10 w-10 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
            <p className="text-gray-500 dark:text-gray-400">No endpoints matching "{search}"</p>
          </div>
        )}

        {Array.from(filteredGroups.entries()).map(([tag, endpoints]) => (
          <div key={tag} className="card overflow-hidden">
            {/* Tag header */}
            <button
              onClick={() => toggleTag(tag)}
              className="w-full flex items-center justify-between px-5 py-3 bg-gray-50 dark:bg-gray-800/50 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              <div className="flex items-center gap-3">
                {expandedTags.has(tag) ? (
                  <ChevronDownIcon className="h-4 w-4 text-gray-400" />
                ) : (
                  <ChevronRightIcon className="h-4 w-4 text-gray-400" />
                )}
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">{tag}</h2>
                <span className="text-xs bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
                  {endpoints.length}
                </span>
              </div>
            </button>

            {/* Endpoints */}
            {expandedTags.has(tag) && (
              <div className="divide-y divide-gray-100 dark:divide-gray-800">
                {endpoints.map((ep) => {
                  const id = `${ep.method}-${ep.path}`;
                  const isExpanded = expandedEndpoints.has(id);
                  const colors = methodColors[ep.method] || methodColors.get;
                  const hasAuth = ep.security && ep.security.length > 0;

                  return (
                    <div key={id}>
                      {/* Endpoint row */}
                      <button
                        onClick={() => toggleEndpoint(id)}
                        className={`w-full flex items-center gap-3 px-5 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors text-left ${isExpanded ? colors.bg : ''}`}
                      >
                        <span className={`inline-flex items-center justify-center w-16 rounded text-xs font-bold uppercase py-1 ${methodBadgeColors[ep.method]} text-white`}>
                          {ep.method}
                        </span>
                        <code className="text-sm font-mono text-gray-700 dark:text-gray-300 flex-1 truncate">
                          {ep.path}
                        </code>
                        {hasAuth && (
                          <LockClosedIcon className="h-3.5 w-3.5 text-gray-400 flex-shrink-0" title="Requires authentication" />
                        )}
                        <span className="text-xs text-gray-500 dark:text-gray-400 max-w-xs truncate hidden sm:block">
                          {ep.summary}
                        </span>
                        {isExpanded ? (
                          <ChevronDownIcon className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        ) : (
                          <ChevronRightIcon className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        )}
                      </button>

                      {/* Expanded detail */}
                      {isExpanded && (
                        <div className={`px-5 py-4 border-t ${colors.border} ${colors.bg}`}>
                          {/* Summary & description */}
                          {ep.summary && (
                            <p className="text-sm font-medium text-gray-900 dark:text-white mb-1">{ep.summary}</p>
                          )}
                          {ep.description && (
                            <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{ep.description}</p>
                          )}

                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {/* Parameters */}
                            {ep.parameters && ep.parameters.length > 0 && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-2">
                                  Parameters
                                </h4>
                                <div className="space-y-1.5">
                                  {ep.parameters.map((param) => (
                                    <div key={param.name} className="flex items-center gap-2 text-xs">
                                      <code className="font-mono text-gray-800 dark:text-gray-200 font-medium">{param.name}</code>
                                      <span className="text-gray-400">({param.in})</span>
                                      {param.required && (
                                        <span className="text-red-500 text-[10px] font-semibold">required</span>
                                      )}
                                      {param.schema?.type && (
                                        <span className="text-primary-600 dark:text-primary-400">{param.schema.type}</span>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Request body */}
                            {ep.requestBody?.content && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-2">
                                  Request Body
                                  {ep.requestBody.required && <span className="text-red-500 ml-1">*</span>}
                                </h4>
                                {Object.entries(ep.requestBody.content).map(([contentType, content]) => (
                                  <div key={contentType}>
                                    <span className="text-[10px] text-gray-400 font-mono">{contentType}</span>
                                    {content.schema && (
                                      <pre className="mt-1 text-xs font-mono bg-gray-900 text-green-400 rounded p-2 overflow-x-auto max-h-32">
                                        {renderSchema(content.schema)}
                                      </pre>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}

                            {/* Responses */}
                            {ep.responses && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-2">
                                  Responses
                                </h4>
                                <div className="space-y-1">
                                  {Object.entries(ep.responses).map(([code, resp]) => (
                                    <div key={code} className="flex items-center gap-2 text-xs">
                                      <span className={`font-mono font-bold ${code.startsWith('2') ? 'text-green-600 dark:text-green-400' : code.startsWith('4') ? 'text-red-600 dark:text-red-400' : 'text-gray-500'}`}>
                                        {code}
                                      </span>
                                      <span className="text-gray-500 dark:text-gray-400">{resp.description}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* cURL */}
                            <div>
                              <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-2">
                                cURL
                              </h4>
                              <div className="relative">
                                <pre className="text-xs font-mono bg-gray-900 text-gray-300 rounded p-2 overflow-x-auto max-h-24">
                                  {generateCurl(ep)}
                                </pre>
                                <button
                                  onClick={() => copyToClipboard(generateCurl(ep), `curl-${id}`)}
                                  className="absolute top-1.5 right-1.5 p-1 text-gray-500 hover:text-white transition-colors"
                                  title="Copy cURL"
                                >
                                  {copiedId === `curl-${id}` ? (
                                    <CheckIcon className="h-3.5 w-3.5 text-green-400" />
                                  ) : (
                                    <ClipboardIcon className="h-3.5 w-3.5" />
                                  )}
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Schema section */}
      {spec.components?.schemas && Object.keys(spec.components.schemas).length > 0 && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <GlobeAltIcon className="h-5 w-5 text-gray-400" />
            Schemas
            <span className="text-xs bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 px-2 py-0.5 rounded-full">
              {Object.keys(spec.components.schemas).length}
            </span>
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(spec.components.schemas).map(([name, schema]) => {
              const props = (schema.properties as Record<string, unknown>) || {};
              const propCount = Object.keys(props).length;
              const required = (schema.required as string[]) || [];
              return (
                <div key={name} className="card p-4">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white font-mono">{name}</h3>
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                    {propCount} fields, {required.length} required
                  </p>
                  <div className="mt-2 space-y-0.5 max-h-32 overflow-y-auto">
                    {Object.entries(props).slice(0, 12).map(([key, val]) => {
                      const prop = val as Record<string, unknown>;
                      const isReq = required.includes(key);
                      return (
                        <div key={key} className="flex items-center gap-1.5 text-[11px]">
                          <span className={`font-mono ${isReq ? 'text-gray-800 dark:text-gray-200 font-medium' : 'text-gray-500 dark:text-gray-400'}`}>
                            {key}
                          </span>
                          <span className="text-primary-500 dark:text-primary-400">
                            {prop.$ref ? (prop.$ref as string).split('/').pop() : (prop.type as string) || 'any'}
                          </span>
                        </div>
                      );
                    })}
                    {Object.keys(props).length > 12 && (
                      <p className="text-[10px] text-gray-400">+{Object.keys(props).length - 12} more</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="mt-8 pb-6 text-center text-xs text-gray-400 dark:text-gray-500">
        Generated from OpenAPI spec at <code className="font-mono">/api/openapi.json</code>
      </div>
    </div>
  );
}

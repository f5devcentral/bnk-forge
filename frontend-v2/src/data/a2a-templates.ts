/**
 * A2A Template definitions — pre-built Gateway API + iRule configurations
 * for common A2A traffic patterns.
 *
 * Sourced from the datkube-a2a-demo project.
 */

export interface A2ATemplate {
  id: string;
  name: string;
  description: string;
  category: 'gateway' | 'irule' | 'security';
  tags: string[];
  yaml: string;
}

export const a2aTemplates: A2ATemplate[] = [
  {
    id: 'a2a-gateway-simple',
    name: 'A2A Gateway — Simple Request/Response',
    description: 'GatewayClass + Gateway + HTTPRoute for A2A JSON-RPC traffic. Routes agent requests to backend services with SSE support for streaming responses.',
    category: 'gateway',
    tags: ['GatewayClass', 'Gateway', 'HTTPRoute', 'JSON-RPC'],
    yaml: `apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: a2a-gatewayclass
spec:
  controllerName: f5.com/default-f5-cne-controller
  description: F5 BIG-IP Kubernetes Gateway for A2A traffic
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: a2a-gateway
spec:
  addresses:
  - type: IPAddress
    value: 11.11.11.10     # <-- Replace with your VIP
  gatewayClassName: a2a-gatewayclass
  listeners:
  - allowedRoutes:
      kinds:
      - kind: HTTPRoute
    name: a2a-listener
    port: 10001
    protocol: HTTP
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: a2a-httproute
  annotations:
    "k8s.f5.com/sse-enabled": "true"  # Required for streaming
spec:
  parentRefs:
  - name: a2a-gateway
    sectionName: a2a-listener
  rules:
  - backendRefs:
    - name: my-agent-service   # <-- Replace with your agent service
      port: 10001`,
  },
  {
    id: 'a2a-gateway-longrun',
    name: 'A2A Gateway — Long-Running Tasks with Push',
    description: 'Dual-gateway setup for long-running A2A tasks with push notifications. Gateway 1 handles agent requests, Gateway 2 receives push notification callbacks.',
    category: 'gateway',
    tags: ['Gateway', 'HTTPRoute', 'Push Notifications', 'Dual VIP'],
    yaml: `# Gateway 1 — Agent requests (client → agent)
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: a2a-gw-agent
spec:
  addresses:
  - type: IPAddress
    value: 11.11.11.20     # <-- Agent VIP
  gatewayClassName: a2a-gatewayclass
  listeners:
  - allowedRoutes:
      kinds:
      - kind: HTTPRoute
    name: a2a-agent
    port: 4000
    protocol: HTTP
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: a2a-agent-route
  annotations:
    "k8s.f5.com/sse-enabled": "true"
spec:
  parentRefs:
  - name: a2a-gw-agent
    sectionName: a2a-agent
  rules:
  - backendRefs:
    - name: my-agent-service   # <-- Replace
      port: 4000
---
# Gateway 2 — Push notification callbacks (agent → client)
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: a2a-gw-callback
spec:
  addresses:
  - type: IPAddress
    value: 22.22.22.20     # <-- Callback VIP
  gatewayClassName: a2a-gatewayclass
  listeners:
  - allowedRoutes:
      kinds:
      - kind: HTTPRoute
    name: a2a-callback
    port: 8888
    protocol: HTTP
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: a2a-callback-route
  annotations:
    "k8s.f5.com/sse-enabled": "true"
spec:
  parentRefs:
  - name: a2a-gw-callback
    sectionName: a2a-callback
  rules:
  - backendRefs:
    - name: my-callback-service  # <-- Replace
      port: 8888`,
  },
  {
    id: 'a2a-irule-inspection',
    name: 'A2A JSON-RPC Inspection iRule',
    description: 'Inspects A2A JSON-RPC requests and responses at Layer 7. Logs agent card discovery, message/send requests, task results, and error responses with structured fields.',
    category: 'irule',
    tags: ['F5BigCneIrule', 'JSON_REQUEST', 'JSON_RESPONSE', 'Logging'],
    yaml: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneIrule
metadata:
  name: a2a-inspection-irule
spec:
  iRule: |
    when HTTP_REQUEST {
      set req_path [HTTP::path]
      log local0. "A2A request to path \${req_path}"
    }

    when JSON_REQUEST {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      if { \$req_path eq "/" } {
        if { [JSON::object get \$root_obj method string] eq "message/send" } {
          set req_id [JSON::object get \$root_obj id string]
          set params_obj [JSON::object get \$root_obj params object]
          set msg_obj [JSON::object get \$params_obj message object]
          set role [JSON::object get \$msg_obj role string]
          log local0. "A2A SendMessage id=\${req_id} role=\${role}"
        }
      }
    }

    when JSON_RESPONSE {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      if { \$req_path eq "/.well-known/agent-card.json" } {
        set agent_name [JSON::object get \$root_obj name string]
        log local0. "A2A Agent Card: \${agent_name}"
      } elseif { \$req_path eq "/" } {
        set root_keys [JSON::object keys \$root_obj]
        if { [lsearch -exact \$root_keys "result"] != -1 } {
          set result_obj [JSON::object get \$root_obj result object]
          set status_obj [JSON::object get \$result_obj status object]
          set state [JSON::object get \$status_obj state string]
          log local0. "A2A Response state=\${state}"
        }
      }
    }
---
# Attach to gateway via BNKNetPolicy
apiVersion: gateway.k8s.f5net.com/v1alpha1
kind: BNKNetPolicy
metadata:
  name: a2a-inspection-policy
spec:
  extensionRefs:
  - name: a2a-inspection-irule
  targetRefs:
  - name: a2a-gateway          # <-- Replace with your gateway
    sectionName: a2a-listener   # <-- Replace with your listener`,
  },
  {
    id: 'a2a-irule-session-persistence',
    name: 'A2A Session Persistence iRule',
    description: 'Pins long-running A2A tasks to the same backend server using task ID-based persistence. Stores task ID → server mapping in the session table with a 5-minute TTL.',
    category: 'irule',
    tags: ['F5BigCneIrule', 'Session Persistence', 'Task ID', 'table set/lookup'],
    yaml: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneIrule
metadata:
  name: a2a-persistence-irule
spec:
  iRule: |
    when JSON_REQUEST {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      if { \$req_path eq "/a2a/jsonrpc" } {
        # Extract task ID from params.id
        set params_obj [JSON::object get \$root_obj params object]
        set task_id ""
        catch { set task_id [JSON::object get \$params_obj id string] }
        if { \$task_id ne "" } {
          # Look up persisted server for this task
          set ip_port [table lookup \$task_id]
          if { \$ip_port ne "" } {
            log local0. "A2A persistence: task \${task_id} → \${ip_port}"
            node \$ip_port
          }
        }
      }
    }

    when JSON_RESPONSE {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      if { \$req_path eq "/a2a/jsonrpc" } {
        # Store task ID → server mapping from response
        set root_keys [JSON::object keys \$root_obj]
        if { [lsearch -exact \$root_keys "result"] != -1 } {
          set result_obj [JSON::object get \$root_obj result object]
          set result_id ""
          catch { set result_id [JSON::object get \$result_obj id string] }
          if { \$result_id ne "" } {
            # Pin task to this server for 300s
            table set \$result_id "[IP::remote_addr]:[TCP::remote_port]" 300
            log local0. "A2A persist: \${result_id} → [IP::remote_addr]:[TCP::remote_port]"
          }
        }
      }
    }

    when HTTP_REQUEST {
      set req_path [HTTP::path]
    }`,
  },
  {
    id: 'a2a-irule-jwt',
    name: 'A2A JWT Validation iRule',
    description: 'Validates Bearer JWT tokens on A2A requests using RS256 algorithm and JWK keys. Returns 403 for invalid tokens. Pairs with the JWK ConfigMap template.',
    category: 'security',
    tags: ['F5BigCneIrule', 'JWT', 'OAuth', 'RS256', 'Security'],
    yaml: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneIrule
metadata:
  name: a2a-jwt-irule
spec:
  iRule: |
    when HTTP_REQUEST {
      set auth_header [HTTP::header Authorization]
      if { [string match -nocase "Bearer *" \$auth_header] } {
        set jwt [string trim [string range \$auth_header 7 end]]
        set res [ACCESS::oauth validate \\
          -payload \$jwt \\
          -alg RS256 \\
          -key jwk-1-for-irule \\
          -iss "https://your-issuer.example.com" \\
          -aud "https://api.example.com"]
        log local0. "JWT validation: \$res"
        if { !(\$res contains {"is_valid":true}) } {
          HTTP::respond 403 content "Forbidden: Invalid JWT"
          return
        }
      } else {
        log local0. "No Bearer token — allowing request"
      }
    }
---
# JWK ConfigMap for JWT validation
apiVersion: v1
kind: ConfigMap
metadata:
  name: f5-big-oauth-jwk-configs
  labels:
    f5.com/config-type: oauth-jwk
data:
  jwk-1-for-irule: |
    {
      "kty": "RSA",
      "n": "your-rsa-modulus-here",
      "e": "AQAB",
      "alg": "RS256",
      "kid": "jwk-1-for-irule"
    }`,
  },
  {
    id: 'a2a-irule-url-rewrite',
    name: 'A2A URL Rewriting iRule',
    description: 'Rewrites agent-card URLs and push notification callback URLs to use the BNK Gateway VIP address instead of internal pod IPs. Essential for external client access.',
    category: 'irule',
    tags: ['F5BigCneIrule', 'URL Rewrite', 'Push Notifications', 'Agent Card'],
    yaml: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneIrule
metadata:
  name: a2a-url-rewrite-irule
spec:
  iRule: |
    when CLIENT_ACCEPTED {
      set vip_addr [IP::local_addr]
    }

    when HTTP_REQUEST {
      set req_path [HTTP::path]
    }

    when JSON_REQUEST {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      # Rewrite push notification callback URLs
      if { \$req_path eq "/a2a/jsonrpc" } {
        catch {
          set params_obj [JSON::object get \$root_obj params object]
          set config_obj [JSON::object get \$params_obj configuration object]
          set push_obj [JSON::object get \$config_obj pushNotificationConfig object]
          set push_url [JSON::object get \$push_obj url string]
          if { \$push_url ne "" } {
            set src_addr [URI::host \$push_url]
            set new_url [string map [list \$src_addr \$vip_addr] \$push_url]
            JSON::object set \$push_obj "url" "string" \$new_url
            log local0. "Rewrote push URL: \$push_url → \$new_url"
          }
        }
      }
    }

    when JSON_RESPONSE {
      set root [JSON::root]
      set root_obj [JSON::get \$root object]
      # Rewrite agent-card URL to use Gateway VIP
      if { \$req_path eq "/.well-known/agent-card.json" } {
        catch {
          set url [JSON::object get \$root_obj url string]
          set src_addr [URI::host \$url]
          set new_url [string map [list \$src_addr \$vip_addr] \$url]
          JSON::object set \$root_obj "url" "string" \$new_url
          log local0. "Rewrote agent-card URL: \$url → \$new_url"
        }
      }
    }`,
  },
];

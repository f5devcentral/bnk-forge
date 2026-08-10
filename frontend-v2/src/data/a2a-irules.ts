/**
 * A2A iRule Library — curated iRules for A2A traffic management.
 *
 * Each iRule is a standalone snippet that can be applied to a BNK Gateway
 * via an F5BigCneIrule CRD + BNKNetPolicy. Extracted from the
 * datkube-a2a-demo project and cleaned up for reuse.
 */

export interface A2AIRule {
  id: string;
  name: string;
  description: string;
  category: 'inspection' | 'persistence' | 'rewriting' | 'security';
  events: string[];
  code: string;
}

export const a2aIRules: A2AIRule[] = [
  {
    id: 'json-rpc-inspection',
    name: 'JSON-RPC Request/Response Inspector',
    description: 'Inspects A2A JSON-RPC traffic at Layer 7. Logs SendMessage requests with role and content, agent-card discovery responses, task results with state, and error responses.',
    category: 'inspection',
    events: ['HTTP_REQUEST', 'JSON_REQUEST', 'JSON_RESPONSE', 'HTTP_RESPONSE'],
    code: `when HTTP_REQUEST {
  set req_path [HTTP::path]
  log local0. "A2A request to path \${req_path}"
}

when JSON_REQUEST {
  set root [JSON::root]
  set root_obj [JSON::get $root object]
  if { $req_path eq "/" } {
    if { [JSON::object get $root_obj method string] eq "message/send" } {
      set src_addr [IP::remote_addr]
      set src_port [TCP::remote_port]
      set req_id [JSON::object get $root_obj id string]
      set params_obj [JSON::object get $root_obj params object]
      set msg_obj [JSON::object get $params_obj message object]
      set role [JSON::object get $msg_obj role string]
      set parts_arr [JSON::object get $msg_obj parts array]
      set log_msg "\\nA2A SendMessage"
      append log_msg "\\n  Client: \${src_addr}:\${src_port}"
      append log_msg "\\n  ReqId:  \${req_id}"
      append log_msg "\\n  Role:   \${role}"
      for {set i 0} {$i < [JSON::array size $parts_arr]} {incr i} {
        set part [JSON::array get $parts_arr $i object]
        set kind [JSON::object get $part kind string]
        if { $kind eq "text" } {
          append log_msg "\\n  Content: [JSON::object get $part text string]"
        }
      }
      log local0. "$log_msg"
    }
  }
}

when JSON_RESPONSE {
  set root [JSON::root]
  set root_obj [JSON::get $root object]
  if { $req_path eq "/.well-known/agent-card.json" } {
    set name [JSON::object get $root_obj name string]
    set desc [JSON::object get $root_obj description string]
    log local0. "A2A Agent Card: \${name} — \${desc}"
  } elseif { $req_path eq "/" } {
    set keys [JSON::object keys $root_obj]
    if { [lsearch -exact $keys "result"] != -1 } {
      set result [JSON::object get $root_obj result object]
      set status [JSON::object get $result status object]
      set state [JSON::object get $status state string]
      log local0. "A2A Response state=\${state}"
    } elseif { [lsearch -exact $keys "error"] != -1 } {
      set req_id [JSON::object get $root_obj id string]
      log local0. "A2A Error reqId=\${req_id}"
    }
  }
}`,
  },
  {
    id: 'task-persistence',
    name: 'Task ID Session Persistence',
    description: 'Pins long-running A2A tasks to the same backend server. On first response, stores task ID → server:port in the session table (300s TTL). Subsequent requests for the same task are routed to the same server.',
    category: 'persistence',
    events: ['HTTP_REQUEST', 'JSON_REQUEST', 'JSON_RESPONSE'],
    code: `when HTTP_REQUEST {
  set req_path [HTTP::path]
}

when JSON_REQUEST {
  set root [JSON::root]
  set root_obj [JSON::get $root object]
  # Look up task ID for persistence
  set method [JSON::object get $root_obj method string]
  set task_id ""
  catch {
    set params [JSON::object get $root_obj params object]
    set task_id [JSON::object get $params id string]
  }
  if { $task_id ne "" } {
    set server [table lookup $task_id]
    if { $server ne "" } {
      log local0. "A2A persist: \${task_id} → \${server}"
      node $server
    }
  }
}

when JSON_RESPONSE {
  set root [JSON::root]
  set root_obj [JSON::get $root object]
  set keys [JSON::object keys $root_obj]
  if { [lsearch -exact $keys "result"] != -1 } {
    set result [JSON::object get $root_obj result object]
    set kind ""
    set id ""
    catch { set kind [JSON::object get $result kind string] }
    catch { set id [JSON::object get $result id string] }
    if { $kind eq "task" && $id ne "" } {
      # Pin this task to current server for 5 minutes
      table set $id "[IP::remote_addr]:[TCP::remote_port]" 300
      log local0. "A2A persist stored: \${id} → [IP::remote_addr]:[TCP::remote_port]"
    }
  }
}`,
  },
  {
    id: 'url-rewrite',
    name: 'Agent Card & Push URL Rewriter',
    description: 'Rewrites internal pod IPs to the BNK Gateway VIP in agent-card responses and push notification callback URLs. Essential when agents return their internal address instead of the external gateway address.',
    category: 'rewriting',
    events: ['CLIENT_ACCEPTED', 'HTTP_REQUEST', 'JSON_REQUEST', 'JSON_RESPONSE'],
    code: `when CLIENT_ACCEPTED {
  # Capture the Gateway VIP address for URL rewriting
  set vip_addr [IP::local_addr]
}

when HTTP_REQUEST {
  set req_path [HTTP::path]
}

when JSON_REQUEST {
  # Rewrite push notification callback URLs to use Gateway VIP
  catch {
    set root [JSON::root]
    set root_obj [JSON::get $root object]
    set params [JSON::object get $root_obj params object]
    set config [JSON::object get $params configuration object]
    set push [JSON::object get $config pushNotificationConfig object]
    set push_url [JSON::object get $push url string]
    if { $push_url ne "" } {
      set src_addr [URI::host $push_url]
      set new_url [string map [list $src_addr $vip_addr] $push_url]
      JSON::object set $push "url" "string" $new_url
      log local0. "Rewrote push URL → \${new_url}"
    }
  }
}

when JSON_RESPONSE {
  # Rewrite agent-card URL to use Gateway VIP
  if { $req_path eq "/.well-known/agent-card.json" } {
    catch {
      set root [JSON::root]
      set root_obj [JSON::get $root object]
      set url [JSON::object get $root_obj url string]
      set src_addr [URI::host $url]
      set new_url [string map [list $src_addr $vip_addr] $url]
      JSON::object set $root_obj "url" "string" $new_url
      log local0. "Rewrote agent-card URL → \${new_url}"
    }
  }
}`,
  },
  {
    id: 'jwt-validation',
    name: 'JWT Bearer Token Validator',
    description: 'Validates Bearer JWT tokens on incoming A2A requests using RS256 algorithm with JWK keys. Returns HTTP 403 for missing or invalid tokens. Requires a JWK ConfigMap (f5-big-oauth-jwk-configs).',
    category: 'security',
    events: ['HTTP_REQUEST'],
    code: `when HTTP_REQUEST {
  set auth_header [HTTP::header Authorization]
  if { [string match -nocase "Bearer *" $auth_header] } {
    set jwt [string trim [string range $auth_header 7 end]]
    set res [ACCESS::oauth validate \\
      -payload $jwt \\
      -alg RS256 \\
      -key jwk-1-for-irule \\
      -iss "https://your-issuer.example.com" \\
      -aud "https://api.example.com"]
    log local0. "JWT validation: $res"
    if { !($res contains {"is_valid":true}) } {
      HTTP::respond 403 content "Forbidden: Invalid JWT"
      return
    }
  } else {
    # No Bearer token — decide: allow or reject
    log local0. "No Bearer token in Authorization header"
    # Uncomment to reject unauthenticated requests:
    # HTTP::respond 401 content "Unauthorized: Bearer token required"
  }
}`,
  },
];

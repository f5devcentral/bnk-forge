import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { YamlEditor } from './YamlEditor';
import { Loader2, FileCode } from 'lucide-react';

interface ResourceCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (resourceYaml: string, dryRun: boolean) => void;
  isLoading?: boolean;
  resourceType?: string;
  namespace?: string;
}

const getDefaultYaml = (resourceType: string = 'pod', namespace: string = 'default') => {
  const templates: Record<string, string> = {
    pod: `apiVersion: v1
kind: Pod
metadata:
  name: my-pod
  namespace: ${namespace}
spec:
  containers:
  - name: nginx
    image: nginx:latest
    ports:
    - containerPort: 80`,

    deployment: `apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-deployment
  namespace: ${namespace}
spec:
  replicas: 3
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80`,

    statefulset: `apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: my-statefulset
  namespace: ${namespace}
spec:
  serviceName: my-service
  replicas: 3
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: nginx
        image: nginx:latest
        ports:
        - containerPort: 80`,

    daemonset: `apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: my-daemonset
  namespace: ${namespace}
spec:
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: nginx
        image: nginx:latest`,

    replicaset: `apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: my-replicaset
  namespace: ${namespace}
spec:
  replicas: 3
  selector:
    matchLabels:
      app: myapp
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
      - name: nginx
        image: nginx:latest`,

    service: `apiVersion: v1
kind: Service
metadata:
  name: my-service
  namespace: ${namespace}
spec:
  selector:
    app: myapp
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8080
  type: ClusterIP`,

    ingress: `apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-ingress
  namespace: ${namespace}
spec:
  rules:
  - host: example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: my-service
            port:
              number: 80`,

    gateway: `apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: my-gateway
  namespace: ${namespace}
spec:
  gatewayClassName: f5-gateway-class
  listeners:
  - name: http
    port: 80
    protocol: HTTP`,

    httproute: `apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-httproute
  namespace: ${namespace}
spec:
  parentRefs:
  - name: my-gateway
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: my-service
      port: 80`,

    grpcroute: `apiVersion: gateway.networking.k8s.io/v1alpha2
kind: GRPCRoute
metadata:
  name: my-grpcroute
  namespace: ${namespace}
spec:
  parentRefs:
  - name: my-gateway
  rules:
  - backendRefs:
    - name: my-service
      port: 9090`,

    tcproute: `apiVersion: gateway.networking.k8s.io/v1alpha2
kind: TCPRoute
metadata:
  name: my-tcproute
  namespace: ${namespace}
spec:
  parentRefs:
  - name: my-gateway
  rules:
  - backendRefs:
    - name: my-service
      port: 3306`,

    udproute: `apiVersion: gateway.networking.k8s.io/v1alpha2
kind: UDPRoute
metadata:
  name: my-udproute
  namespace: ${namespace}
spec:
  parentRefs:
  - name: my-gateway
  rules:
  - backendRefs:
    - name: my-service
      port: 53`,

    tlsroute: `apiVersion: gateway.networking.k8s.io/v1alpha2
kind: TLSRoute
metadata:
  name: my-tlsroute
  namespace: ${namespace}
spec:
  parentRefs:
  - name: my-gateway
  rules:
  - backendRefs:
    - name: my-service
      port: 443`,

    referencegrant: `apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: my-referencegrant
  namespace: ${namespace}
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: default
  to:
  - group: ""
    kind: Service`,

    gatewayclass: `apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: my-gatewayclass
spec:
  controllerName: f5.io/gateway-controller`,

    configmap: `apiVersion: v1
kind: ConfigMap
metadata:
  name: my-configmap
  namespace: ${namespace}
data:
  key1: value1
  key2: value2`,

    secret: `apiVersion: v1
kind: Secret
metadata:
  name: my-secret
  namespace: ${namespace}
type: Opaque
stringData:
  username: admin
  password: changeme`,

    persistentvolumeclaim: `apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-pvc
  namespace: ${namespace}
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi`,

    namespace: `apiVersion: v1
kind: Namespace
metadata:
  name: my-namespace`,

    // ─── F5 BNK CRD Templates ───────────────────────────────────────────
    // Each template includes helpful comments explaining key fields.
    // Defaults follow F5 CloudDocs best practices and the BACKLOG spec.

    f5bigfwpolicy: `apiVersion: k8s.f5net.com/v1
kind: F5BigFwPolicy
metadata:
  name: my-fw-policy
  namespace: ${namespace}
spec:
  # Rules are evaluated in order; first match wins.
  # Best practice: explicit allow rules first, then a deny-all catch-all.
  rule:
  - name: allow-http
    # action: accept | drop | reject
    action: accept
    # ipProtocol: tcp | udp | any
    ipProtocol: tcp
    destination:
      # Reference address/port lists by name (F5BigCneAddresslist / F5BigCnePortlist)
      addressLists: []
      addresses: []
      portLists:
      - my-port-list
      ports: []
    source:
      addressLists:
      - my-address-list
      addresses: []
      portLists: []
      ports: []
    # Enable logging for audit trail (requires F5BigLogProfile)
    logging: false
  - name: deny-all
    # Safe default: drop all unmatched traffic
    action: drop
    ipProtocol: any
    destination:
      addressLists: []
      addresses: []
      portLists: []
      ports: []
    source:
      addressLists: []
      addresses: []
      portLists: []
      ports: []
    logging: false`,

    f5bigfwrulelist: `apiVersion: k8s.f5net.com/v1
kind: F5BigFwRulelist
metadata:
  name: my-rule-list
  namespace: ${namespace}
spec:
  # Reusable rule list — can be referenced by multiple F5BigFwPolicy resources.
  rule:
  - name: allow-web
    action: accept
    ipProtocol: tcp
    destination:
      addressLists: []
      addresses: []
      portLists: []
      # Ports must be strings
      ports:
      - "80"
      - "443"
    source:
      addressLists: []
      addresses: []
      portLists: []
      ports: []
    logging: false`,

    f5bigcneaddresslist: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneAddresslist
metadata:
  name: my-address-list
  namespace: ${namespace}
spec:
  # CIDR notation. Used by F5BigFwPolicy source/destination addressLists.
  addresses:
  - 10.0.0.0/8
  - 172.16.0.0/12
  - 192.168.0.0/16`,

    f5bigcneportlist: `apiVersion: k8s.f5net.com/v1
kind: F5BigCnePortlist
metadata:
  name: my-port-list
  namespace: ${namespace}
spec:
  # Port numbers as strings. Used by F5BigFwPolicy source/destination portLists.
  ports:
  - "80"
  - "443"
  - "8080"`,

    f5bigcneirule: `apiVersion: k8s.f5net.com/v1
kind: F5BigCneIrule
metadata:
  name: my-irule
  namespace: ${namespace}
spec:
  # TCL-based iRule for custom traffic manipulation.
  # Attach to a Gateway via BNKNetPolicy extensionRefs.
  iRule: |
    when HTTP_REQUEST {
      # Log incoming requests (local0 = syslog facility)
      log local0. "REQUEST host=[HTTP::host] uri=[HTTP::uri] method=[HTTP::method]"
    }
    when HTTP_RESPONSE {
      # Log response status codes
      log local0. "RESPONSE status=[HTTP::status]"
    }`,

    f5spkvlan: `apiVersion: k8s.f5net.com/v1
kind: F5SPKVlan
metadata:
  name: my-vlan
  namespace: ${namespace}
spec:
  # Logical name referenced by F5SPKStaticRoute and F5SPKEgress
  name: external
  # TMM interface index (matches NetworkAttachmentDefinition order)
  interfaces:
  - "1.1"
  # Self-IP for TMM on this VLAN (must be routable from your network)
  selfip_v4s:
  - 10.0.10.240
  # MTU: 1500 (standard) or 9000 (jumbo frames for high-throughput)
  mtu: 1500`,

    f5spkstaticroute: `apiVersion: k8s.f5net.com/v1
kind: F5SPKStaticRoute
metadata:
  name: my-static-route
  namespace: ${namespace}
spec:
  # 0.0.0.0/0.0.0.0 = default route (all traffic)
  destination: 0.0.0.0
  netmask: 0.0.0.0
  # Next-hop gateway IP
  gateway: 10.0.10.1
  # Must match an F5SPKVlan spec.name
  vlan: external`,

    f5spksnatpool: `apiVersion: k8s.f5net.com/v1
kind: F5SPKSnatpool
metadata:
  name: my-snatpool
  namespace: ${namespace}
spec:
  # Source NAT addresses — outbound traffic will use these IPs.
  # Must be routable from the external network.
  members:
  - 10.0.10.250
  - 10.0.10.251`,

    f5spkegress: `apiVersion: k8s.f5net.com/v3
kind: F5SPKEgress
metadata:
  name: my-egress
  namespace: ${namespace}
spec:
  # SNAT pool reference for source address translation.
  # Alternative: use sourceTranslationType: SRC_TRANS_AUTOMAP (no snatPoolRef needed)
  snatPoolRef:
    name: my-snatpool
  # Egress routing — where to send outbound traffic
  routes:
  - destination: 0.0.0.0/0
    gateway: 10.0.10.1`,

    bnksecpolicy: `apiVersion: gateway.k8s.f5net.com/v1alpha1
kind: BNKSecPolicy
metadata:
  name: my-sec-policy
  namespace: ${namespace}
spec:
  # Attach this security policy to a Gateway listener
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    # Name of your Gateway resource
    name: my-gateway
    # Listener name to attach to (must match Gateway spec.listeners[].name)
    sectionName: http
  # Reference an F5BigFwPolicy for firewall rules
  policy:
    firewallPolicy:
      name: my-fw-policy`,

    bnknetpolicy: `apiVersion: gateway.k8s.f5net.com/v1alpha1
kind: BNKNetPolicy
metadata:
  name: my-net-policy
  namespace: ${namespace}
spec:
  # Attach to a Gateway listener
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: my-gateway
    sectionName: http
  # Extension references — attach iRules, TCP settings, HSL logging, etc.
  extensionRefs:
  - group: k8s.f5net.com
    kind: F5BigCneIrule
    name: my-irule`,

    f5bigddosglobal: `apiVersion: k8s.f5net.com/v1
kind: F5BigDdosGlobal
metadata:
  name: my-ddos-global
  namespace: ${namespace}
spec:
  # Bad Actor Detection — automatically detects and rate-limits abusive source IPs
  badActorDetection:
    enabled: true
    perSourceIpLimit: 100
    detectionThreshold: 80
    mitigationRate: 500
  # Attack vectors — each vector targets a specific flood type.
  # autoThreshold + fully-automatic = BIG-IP tunes thresholds dynamically.
  vectors:
  # TCP SYN Flood Protection
  - name: tcp-syn-flood
    autoThreshold: true
    thresholdMode: fully-automatic
    detectionThresholdPercent: 500
    mitigation:
      autoMitigate: true
  # UDP Flood Protection
  - name: udp-flood
    autoThreshold: true
    thresholdMode: fully-automatic
    detectionThresholdPercent: 500
    mitigation:
      autoMitigate: true
  # DNS Query Flood Protection
  - name: dns-query-flood
    autoThreshold: true
    thresholdMode: fully-automatic
    detectionThresholdPercent: 500
    mitigation:
      autoMitigate: true`,

    f5bigloghslpub: `apiVersion: k8s.f5net.com/v2
kind: F5BigLogHslpub
metadata:
  name: my-hsl-publisher
  namespace: ${namespace}
spec:
  # High-Speed Logging publisher — sends log data to an external syslog server.
  # protocol: udp | tcp
  protocol: udp
  # Pool name for log destination servers
  pool: my-log-pool
  # Syslog settings (optional — uncomment to customize)
  # syslog:
  #   format: rfc5424
  #   distribution: adaptive`,

    f5biglogprofile: `apiVersion: k8s.f5net.com/v2
kind: F5BigLogProfile
metadata:
  name: my-log-profile
  namespace: ${namespace}
spec:
  # Reference an F5BigLogHslpub for log delivery
  hslPublisher: my-hsl-publisher
  # Enable network security logging (firewall rule hits, drops, etc.)
  networkSecurity:
    enabled: true`,

    l4route: `apiVersion: gateway.k8s.f5net.com/v1
kind: L4Route
metadata:
  name: my-l4-route
  namespace: ${namespace}
spec:
  # Attach to a Gateway listener (must be a TCP/UDP listener, not HTTP)
  parentRefs:
  - name: my-gateway
    # sectionName must match a Gateway listener name
    sectionName: tcp
  rules:
  - backendRefs:
    - name: my-service
      port: 3306`,

    f5spkglobaloptions: `apiVersion: k8s.f5net.com/v1
kind: F5BigGlobalOptions
metadata:
  name: global-options
  namespace: ${namespace}
spec:
  # Global TMM configuration options.
  # Typically used for crypto hardware acceleration on DPU deployments.
  # Leave spec empty ({}) for standard software-only deployments.
  {}`,

    // ─── Additional F5 BNK CRD Templates (cneinstance, f5bnkgateway, ipamrange) ──

    cneinstance: `apiVersion: k8s.f5.com/v1
kind: CNEInstance
metadata:
  name: bnk-instance
  namespace: ${namespace}
spec:
  # Watch all namespaces for Gateway resources
  wholeCluster: true
  # DPU mode — set to true only for NVIDIA BlueField deployments
  dpu:
    enabled: false
  # Network Attachment Definitions — must match your Multus NAD names
  networkAttachments:
  - external-netdevice
  - internal-netdevice
  # Feature toggles — each MUST be an object with "enabled" key (not bare boolean)
  firewallACL:
    enabled: true
  intelligentLB:
    enabled: false
  pseudoCNI:
    enabled: true
  metricSubsystem:
    enabled: false
  loggingSubsystem:
    enabled: false
  envDiscovery:
    enabled: false
  # TMM environment variables
  tmm:
    env:
    - name: TMM_DEFAULT_MTU
      value: "9000"
    - name: TMM_IGNORE_GATEWAYS
      value: "TRUE"`,

    f5bnkgateway: `apiVersion: k8s.f5net.com/v1
kind: F5BnkGateway
metadata:
  name: my-bnk-gateway
  namespace: ${namespace}
spec:
  # IPAM integration — automatically assigns an external IP to your Gateway.
  # The F5 IPAM Controller watches this CR and allocates from the referenced IPAMRange.
  gatewayRef:
    # Name of the standard Gateway API Gateway resource
    name: my-gateway
    namespace: ${namespace}
  # IPAM label selector — must match an IPAMRange spec.ipamLabel
  ipamLabel: my-ipam-pool`,

    ipamrange: `apiVersion: fic.f5.com/v1
kind: IPAMRange
metadata:
  name: my-ipam-range
  namespace: ${namespace}
spec:
  # Label used to match F5BnkGateway requests to this pool
  ipamLabel: my-ipam-pool
  # IP address ranges available for allocation (CIDR or range notation)
  ranges:
  - range: 10.0.20.100-10.0.20.200
  # Optional: specify a default gateway for allocated IPs
  # defaultGateway: 10.0.20.1`,

    f5ipamprovider: `apiVersion: fic.f5.com/v1
kind: F5IPAMProvider
metadata:
  name: my-ipam-provider
  namespace: ${namespace}
spec:
  # IPAM Provider configuration — the F5 IPAM Controller uses this to manage IP allocation.
  # providerName must match what F5BnkGateway expects (usually "f5-ipam")
  providerName: f5-ipam
  # Parameters for IPAM provider (provider-specific)
  parameters:
    # IP address ranges for allocation
    ipRange: 10.0.20.100-10.0.20.200
    # Optional: CIDR notation alternative
    # cidr: 10.0.20.0/24`,

    bnkgatewayclassconfig: `apiVersion: gateway.k8s.f5net.com/v1alpha1
kind: BNKGatewayClassConfig
metadata:
  name: my-gateway-class-config
  # Note: BNKGatewayClassConfig is typically cluster-scoped (no namespace)
spec:
  # Configuration for GatewayClass behavior across all Gateways using it.
  # This CR is referenced by GatewayClass.spec.parametersRef.
  
  # Default listener settings (can be overridden per-Gateway)
  defaultListenerSettings:
    # Connection timeout in seconds
    connectionTimeout: 60
    # Idle timeout in seconds
    idleTimeout: 300
  
  # TMM resource allocation hints (optional)
  # resourceProfile: high-performance
  
  # Default TLS settings for HTTPS listeners
  # tls:
  #   minVersion: TLSv1.2
  #   cipherSuites:
  #   - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
  #   - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256`,
  };

  return templates[resourceType] || templates.pod;
};

export function ResourceCreateDialog({
  open,
  onOpenChange,
  onSubmit,
  isLoading = false,
  resourceType = 'pod',
  namespace = 'default',
}: ResourceCreateDialogProps) {
  const [resourceYaml, setResourceYaml] = useState(getDefaultYaml(resourceType, namespace));
  const [dryRun, setDryRun] = useState(false);

  // Update YAML when resourceType or namespace changes
  useEffect(() => {
    if (open) {
      setResourceYaml(getDefaultYaml(resourceType, namespace));
    }
  }, [resourceType, namespace, open]);

  const handleSubmit = () => {
    onSubmit(resourceYaml, dryRun);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Create {resourceType.charAt(0).toUpperCase() + resourceType.slice(1)}</DialogTitle>
          <DialogDescription>
            Define your {resourceType} resource using YAML. The resource will be created in namespace <code>{namespace}</code>.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-auto py-4">
          <YamlEditor
            value={resourceYaml}
            onChange={setResourceYaml}
            height="500px"
            showValidation
          />
        </div>

        <DialogFooter className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Checkbox
              id="dryRun"
              checked={dryRun}
              onCheckedChange={(checked) => setDryRun(checked as boolean)}
            />
            <Label htmlFor="dryRun" className="text-sm text-muted-foreground cursor-pointer">
              Dry run (preview changes without applying)
            </Label>
          </div>

          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {dryRun ? 'Validating...' : 'Creating...'}
                </>
              ) : (
                <>
                  <FileCode className="mr-2 h-4 w-4" />
                  {dryRun ? 'Dry Run' : 'Create Resource'}
                </>
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

"""
Kubernetes resource registry for BNK-Forge.
Defines all supported Kubernetes resource types for cloud-agnostic monitoring.

Organization:
1. Core K8s resources (Pods, Services, etc.)
2. Workload resources (Deployments, StatefulSets, etc.)
3. Networking resources (Ingress, NetworkPolicy, etc.)
4. Storage resources (PV, PVC, StorageClass)
5. Config resources (ConfigMap, Secret, etc.)
6. RBAC resources (Roles, Bindings, ServiceAccounts)
7. Batch resources (Jobs, CronJobs)
8. Autoscaling resources (HPA, PDB)
9. Cluster resources (Nodes, Namespaces, CRDs)
10. Gateway API resources
11. cert-manager resources
12. F5 BNK resources
"""


from core.k8s_types import ApiGroups, K8sResourceType, ResourceCategory


def _core_resource(kind: str, plural: str, display_name: str,
                   description: str, namespaced: bool = True) -> K8sResourceType:
    """Helper for core API (v1) resources."""
    return K8sResourceType(
        api_group=ApiGroups.CORE,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=display_name,
        description=description,
        category=ResourceCategory.CORE
    )


def _apps_resource(kind: str, plural: str, display_name: str, description: str) -> K8sResourceType:
    """Helper for apps/v1 resources."""
    return K8sResourceType(
        api_group=ApiGroups.APPS,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=True,
        display_name=display_name,
        description=description,
        category=ResourceCategory.WORKLOADS
    )


def _rbac_resource(kind: str, plural: str, display_name: str,
                   description: str, namespaced: bool) -> K8sResourceType:
    """Helper for RBAC resources."""
    return K8sResourceType(
        api_group=ApiGroups.RBAC,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=display_name,
        description=description,
        category=ResourceCategory.RBAC
    )


def _gateway_resource(kind: str, plural: str, display_name: str,
                      description: str, api_version: str = "v1",
                      namespaced: bool = True) -> K8sResourceType:
    """Helper for Gateway API resources."""
    return K8sResourceType(
        api_group=ApiGroups.GATEWAY,
        api_version=api_version,
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=display_name,
        description=description,
        category=ResourceCategory.GATEWAY_API
    )


def _f5_resource(kind: str, plural: str, display_name: str,
                 description: str, namespaced: bool = True,
                 api_group: str | None = None, api_version: str = "v1") -> K8sResourceType:
    """Helper for F5 BNK resources. Default API group is k8s.f5net.com (data-plane CRDs)."""
    return K8sResourceType(
        api_group=api_group or ApiGroups.F5_NET,
        api_version=api_version,
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=display_name,
        description=description,
        category=ResourceCategory.F5_BNK
    )


def _certmanager_resource(kind: str, plural: str, display_name: str,
                          description: str, namespaced: bool = True) -> K8sResourceType:
    """Helper for cert-manager resources."""
    return K8sResourceType(
        api_group=ApiGroups.CERT_MANAGER,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=display_name,
        description=description,
        category=ResourceCategory.CERT_MANAGER
    )


# =============================================================================
# RESOURCE REGISTRY
# =============================================================================

RESOURCE_REGISTRY: dict[str, K8sResourceType] = {
    # =========================================================================
    # CORE RESOURCES (v1)
    # =========================================================================
    "pod": _core_resource("Pod", "pods", "Pod", "Kubernetes Pod"),
    "service": _core_resource("Service", "services", "Service", "Kubernetes Service"),
    "configmap": _core_resource("ConfigMap", "configmaps", "ConfigMap", "Kubernetes ConfigMap"),
    "secret": _core_resource("Secret", "secrets", "Secret", "Kubernetes Secret"),
    "serviceaccount": _core_resource("ServiceAccount", "serviceaccounts", "Service Account",
                                     "Kubernetes Service Account for pod identity"),
    "endpoints": _core_resource("Endpoints", "endpoints", "Endpoints",
                                "Service endpoints (IP addresses and ports)"),
    "event": _core_resource("Event", "events", "Event", "Kubernetes cluster events"),
    "limitrange": _core_resource("LimitRange", "limitranges", "Limit Range",
                                 "Default resource limits for namespace"),
    "resourcequota": _core_resource("ResourceQuota", "resourcequotas", "Resource Quota",
                                    "Resource usage limits for namespace"),
    "persistentvolumeclaim": _core_resource("PersistentVolumeClaim", "persistentvolumeclaims",
                                            "PVC", "Kubernetes Persistent Volume Claim"),

    # Cluster-scoped core resources
    "namespace": _core_resource("Namespace", "namespaces", "Namespace",
                                "Kubernetes Namespace", namespaced=False),
    "node": _core_resource("Node", "nodes", "Node", "Kubernetes Node", namespaced=False),
    "persistentvolume": _core_resource("PersistentVolume", "persistentvolumes",
                                       "Persistent Volume", "Kubernetes Persistent Volume",
                                       namespaced=False),

    # =========================================================================
    # WORKLOAD RESOURCES (apps/v1)
    # =========================================================================
    "deployment": _apps_resource("Deployment", "deployments", "Deployment", "Kubernetes Deployment"),
    "statefulset": _apps_resource("StatefulSet", "statefulsets", "StatefulSet", "Kubernetes StatefulSet"),
    "daemonset": _apps_resource("DaemonSet", "daemonsets", "DaemonSet", "Kubernetes DaemonSet"),
    "replicaset": _apps_resource("ReplicaSet", "replicasets", "ReplicaSet", "Kubernetes ReplicaSet"),

    # =========================================================================
    # NETWORKING RESOURCES
    # =========================================================================
    "ingress": K8sResourceType(
        api_group=ApiGroups.NETWORKING,
        api_version="v1",
        kind="Ingress",
        plural="ingresses",
        namespaced=True,
        display_name="Ingress",
        description="Kubernetes Ingress",
        category=ResourceCategory.NETWORKING
    ),
    "networkpolicy": K8sResourceType(
        api_group=ApiGroups.NETWORKING,
        api_version="v1",
        kind="NetworkPolicy",
        plural="networkpolicies",
        namespaced=True,
        display_name="Network Policy",
        description="Kubernetes Network Policy for pod traffic control",
        category=ResourceCategory.NETWORKING
    ),

    # =========================================================================
    # STORAGE RESOURCES
    # =========================================================================
    "storageclass": K8sResourceType(
        api_group=ApiGroups.STORAGE,
        api_version="v1",
        kind="StorageClass",
        plural="storageclasses",
        namespaced=False,
        display_name="Storage Class",
        description="Kubernetes Storage Class for dynamic provisioning",
        category=ResourceCategory.STORAGE
    ),

    # =========================================================================
    # RBAC RESOURCES
    # =========================================================================
    "role": _rbac_resource("Role", "roles", "Role", "Namespace-scoped RBAC role", namespaced=True),
    "rolebinding": _rbac_resource("RoleBinding", "rolebindings", "Role Binding",
                                  "Namespace-scoped RBAC role binding", namespaced=True),
    "clusterrole": _rbac_resource("ClusterRole", "clusterroles", "Cluster Role",
                                  "Cluster-wide RBAC role", namespaced=False),
    "clusterrolebinding": _rbac_resource("ClusterRoleBinding", "clusterrolebindings",
                                         "Cluster Role Binding", "Cluster-wide RBAC role binding",
                                         namespaced=False),

    # =========================================================================
    # BATCH RESOURCES
    # =========================================================================
    "job": K8sResourceType(
        api_group=ApiGroups.BATCH,
        api_version="v1",
        kind="Job",
        plural="jobs",
        namespaced=True,
        display_name="Job",
        description="Kubernetes batch Job",
        category=ResourceCategory.BATCH
    ),
    "cronjob": K8sResourceType(
        api_group=ApiGroups.BATCH,
        api_version="v1",
        kind="CronJob",
        plural="cronjobs",
        namespaced=True,
        display_name="CronJob",
        description="Kubernetes scheduled CronJob",
        category=ResourceCategory.BATCH
    ),

    # =========================================================================
    # AUTOSCALING & POLICY RESOURCES
    # =========================================================================
    "horizontalpodautoscaler": K8sResourceType(
        api_group=ApiGroups.AUTOSCALING,
        api_version="v2",
        kind="HorizontalPodAutoscaler",
        plural="horizontalpodautoscalers",
        namespaced=True,
        display_name="HPA",
        description="Horizontal Pod Autoscaler",
        category=ResourceCategory.AUTOSCALING
    ),
    "poddisruptionbudget": K8sResourceType(
        api_group=ApiGroups.POLICY,
        api_version="v1",
        kind="PodDisruptionBudget",
        plural="poddisruptionbudgets",
        namespaced=True,
        display_name="Pod Disruption Budget",
        description="Pod availability during disruptions",
        category=ResourceCategory.AUTOSCALING
    ),

    # =========================================================================
    # CLUSTER RESOURCES
    # =========================================================================
    "customresourcedefinition": K8sResourceType(
        api_group=ApiGroups.APIEXTENSIONS,
        api_version="v1",
        kind="CustomResourceDefinition",
        plural="customresourcedefinitions",
        namespaced=False,
        display_name="CRD",
        description="Custom Resource Definition",
        category=ResourceCategory.CLUSTER
    ),

    # =========================================================================
    # GATEWAY API RESOURCES
    # =========================================================================
    "gatewayclass": _gateway_resource("GatewayClass", "gatewayclasses", "Gateway Class",
                                      "Gateway API Gateway Class definition", namespaced=False),
    "gateway": _gateway_resource("Gateway", "gateways", "Gateway",
                                 "Gateway API Gateway instance with listeners"),
    "httproute": _gateway_resource("HTTPRoute", "httproutes", "HTTP Route",
                                   "Gateway API HTTP routing configuration"),
    "grpcroute": _gateway_resource("GRPCRoute", "grpcroutes", "GRPC Route",
                                   "Gateway API gRPC routing configuration", api_version="v1alpha2"),
    "tcproute": _gateway_resource("TCPRoute", "tcproutes", "TCP Route",
                                  "Gateway API TCP routing configuration", api_version="v1alpha2"),
    "udproute": _gateway_resource("UDPRoute", "udproutes", "UDP Route",
                                  "Gateway API UDP routing configuration", api_version="v1alpha2"),
    "tlsroute": _gateway_resource("TLSRoute", "tlsroutes", "TLS Route",
                                  "Gateway API TLS routing configuration", api_version="v1alpha2"),
    "referencegrant": _gateway_resource("ReferenceGrant", "referencegrants", "Reference Grant",
                                        "Gateway API cross-namespace reference permissions",
                                        api_version="v1beta1"),

    # =========================================================================
    # CERT-MANAGER RESOURCES
    # =========================================================================
    "certificate": _certmanager_resource("Certificate", "certificates", "Certificate",
                                         "cert-manager Certificate resource"),
    "certificaterequest": _certmanager_resource("CertificateRequest", "certificaterequests",
                                                "Certificate Request", "cert-manager Certificate Request"),
    "issuer": _certmanager_resource("Issuer", "issuers", "Issuer",
                                    "cert-manager namespace-scoped Issuer"),
    "clusterissuer": _certmanager_resource("ClusterIssuer", "clusterissuers", "Cluster Issuer",
                                           "cert-manager cluster-wide Issuer", namespaced=False),

    # =========================================================================
    # MULTUS CNI RESOURCES
    # =========================================================================
    "networkattachmentdefinition": K8sResourceType(
        api_group=ApiGroups.MULTUS,
        api_version="v1",
        kind="NetworkAttachmentDefinition",
        plural="network-attachment-definitions",
        namespaced=True,
        display_name="Network Attachment",
        description="Multus CNI network attachment definition",
        category=ResourceCategory.NETWORKING
    ),

    # =========================================================================
    # F5 BNK RESOURCES — Data-plane CRDs (k8s.f5net.com)
    # These are created by crd-installer and have HYPHENATED plural names.
    # =========================================================================

    # --- Security ---
    "f5bigfwpolicy": _f5_resource(
        "F5BigFwPolicy", "f5-big-fw-policies", "F5 Firewall Policy",
        "F5 BIG-IP firewall policy for ingress/egress traffic control"),
    "f5bigfwrulelist": _f5_resource(
        "F5BigFwRulelist", "f5-big-fw-rulelists", "F5 Firewall Rule List",
        "F5 BIG-IP firewall rule list (reusable set of rules referenced by policies)"),
    "bnksecpolicy": K8sResourceType(
        api_group=ApiGroups.F5_GATEWAY_NET,
        api_version="v1alpha1",
        kind="BNKSecPolicy",
        plural="bnksecpolicies",
        namespaced=True,
        display_name="BNK Security Policy",
        description="F5 BNK security policy for Gateway API integration",
        category=ResourceCategory.F5_BNK
    ),
    "bnknetpolicy": K8sResourceType(
        api_group=ApiGroups.F5_GATEWAY_NET,
        api_version="v1alpha1",
        kind="BNKNetPolicy",
        plural="bnknetpolicies",
        namespaced=True,
        display_name="BNK Network Policy",
        description="F5 BNK general extensions for Gateway API (iRules, TCPSettings, HSL logging)",
        category=ResourceCategory.F5_BNK
    ),
    "f5bigddosglobal": _f5_resource(
        "F5BigDdosGlobal", "f5-big-ddos-globals", "F5 DDoS Protection",
        "Global DDoS protection configuration for traffic scrubbing"),
    "f5bigcneaddresslist": _f5_resource(
        "F5BigCneAddresslist", "f5-big-cne-addresslists", "F5 CNE Address List",
        "Address list for firewall rules and policies"),
    "f5bigcneportlist": _f5_resource(
        "F5BigCnePortlist", "f5-big-cne-portlists", "F5 CNE Port List",
        "Port list for firewall rules and policies"),

    # --- Networking ---
    "f5spkvlan": _f5_resource(
        "F5SPKVlan", "f5-spk-vlans", "F5 VLAN",
        "TMM interface configuration: VLANs, Self IPs, MTU sizes"),
    "f5spkstaticroute": _f5_resource(
        "F5SPKStaticRoute", "f5-spk-staticroutes", "F5 Static Route",
        "Static routing table management for Traffic Management Microkernel"),
    "f5spksnatpool": _f5_resource(
        "F5SPKSnatpool", "f5-spk-snatpools", "F5 SNAT Pool",
        "F5 BNK SNAT pool for source address translation"),
    "f5spkegress": _f5_resource(
        "F5SPKEgress", "f5-spk-egresses", "F5 Egress Config",
        "F5 BNK egress configuration for outbound traffic",
        api_version="v3"),
    "f5bigcneirule": _f5_resource(
        "F5BigCneIrule", "f5-big-cne-irules", "F5 iRule",
        "F5 BNK iRule for custom traffic manipulation logic (TCL)"),
    "f5spkglobaloptions": _f5_resource(
        "F5BigGlobalOptions", "f5-big-global-optionses", "F5 Global Options",
        "Global configuration options for crypto hardware acceleration on DPU"),

    # --- Logging & Telemetry ---
    "f5bigloghslpub": _f5_resource(
        "F5BigLogHslpub", "f5-big-log-hslpubs", "F5 HSL Publisher",
        "High-Speed Logging (HSL) publisher configuration",
        api_version="v2"),
    "f5biglogprofile": _f5_resource(
        "F5BigLogProfile", "f5-big-log-profiles", "F5 Log Profile",
        "F5 BNK log profile configuration for traffic logging",
        api_version="v2"),

    # --- Gateway extensions (k8s.f5net.com) ---
    "f5bnkgateway": _f5_resource(
        "F5BnkGateway", "f5-bnkgateways", "F5 BNK Gateway (IPAM)",
        "F5 BNK Gateway for IPAM-based IP address management on Gateway API"),
    "l4route": K8sResourceType(
        api_group=ApiGroups.F5_GATEWAY_NET,
        api_version="v1",
        kind="L4Route",
        plural="l4routes",
        namespaced=True,
        display_name="L4 Route",
        description="F5 BNK Layer 4 route for TCP/UDP traffic (gateway.k8s.f5net.com)",
        category=ResourceCategory.F5_BNK
    ),

    # =========================================================================
    # F5 BNK RESOURCES — FLO-managed CRDs (k8s.f5.com)
    # These are created by FLO itself and manage the operator lifecycle.
    # =========================================================================
    "cneinstance": K8sResourceType(
        api_group=ApiGroups.F5_K8S,
        api_version="v1",
        kind="CNEInstance",
        plural="cneinstances",
        namespaced=True,
        display_name="CNE Instance",
        description="Cloud Native Engine instance configuration",
        category=ResourceCategory.F5_BNK
    ),

    # --- AI/ML ---
    "f5biganalyzer": _f5_resource(
        "F5BigAnalyzer", "f5-big-analyzers", "F5 AI Analyzer",
        "AI-powered load balancing analyzer for LLM inference workloads (EA feature)",
        api_version="v1alpha1"),

    # --- IPAM (fic.f5.com) ---
    "ipamrange": K8sResourceType(
        api_group=ApiGroups.F5_IPAM,
        api_version="v1",
        kind="IPAMRange",
        plural="ipamranges",
        namespaced=True,
        display_name="IPAM Range",
        description="F5 IPAM Controller IP address range allocation",
        category=ResourceCategory.F5_BNK
    ),

    # =========================================================================
    # NVIDIA DPF RESOURCES — Provisioning (provisioning.dpu.nvidia.com)
    # DPU hardware lifecycle: discovery, inventory, BFB images, provisioning.
    # =========================================================================
    "dpudevice": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUDevice",
        plural="dpudevices",
        namespaced=True,
        display_name="DPU Device",
        description="Physical DPU inventory (serial, BMC IP, PCI addr, conditions)",
        category=ResourceCategory.DPF
    ),
    "dpuset": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUSet",
        plural="dpusets",
        namespaced=True,
        display_name="DPU Set",
        description="Desired state for a group of DPUs (BFB image, flavor, rolling update)",
        category=ResourceCategory.DPF
    ),
    "dpucluster": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUCluster",
        plural="dpuclusters",
        namespaced=True,
        display_name="DPU Cluster",
        description="DPU cluster control plane (Kamaji or static kubeconfig)",
        category=ResourceCategory.DPF
    ),
    "dpu": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPU",
        plural="dpus",
        namespaced=True,
        display_name="DPU",
        description="Individual DPU lifecycle (created by DPUSet controller)",
        category=ResourceCategory.DPF
    ),
    "bfb": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="BFB",
        plural="bfbs",
        namespaced=True,
        display_name="BFB Image",
        description="BlueField Boot image (downloaded from URL, flashed to DPUs)",
        category=ResourceCategory.DPF
    ),
    "dpuflavor": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUFlavor",
        plural="dpuflavors",
        namespaced=True,
        display_name="DPU Flavor",
        description="Config template for DPU system-level settings",
        category=ResourceCategory.DPF
    ),
    "dpunode": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUNode",
        plural="dpunodes",
        namespaced=True,
        display_name="DPU Node",
        description="Node-level grouping of DPUDevices",
        category=ResourceCategory.DPF
    ),
    "dpudiscovery": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUDiscovery",
        plural="dpudiscoveries",
        namespaced=True,
        display_name="DPU Discovery",
        description="Auto-discover DPUs by scanning BMC IP ranges (zero-trust mode)",
        category=ResourceCategory.DPF
    ),
    "dpunodemaintenance": K8sResourceType(
        api_group=ApiGroups.DPF_PROVISIONING,
        api_version="v1alpha1",
        kind="DPUNodeMaintenance",
        plural="dpunodemaintenances",
        namespaced=True,
        display_name="DPU Node Maintenance",
        description="Node drain coordination for DPU maintenance operations",
        category=ResourceCategory.DPF
    ),

    # =========================================================================
    # NVIDIA DPF RESOURCES — Services (svc.dpu.nvidia.com)
    # DPU service lifecycle: Helm charts, service chains, interfaces.
    # =========================================================================
    "dpuservice": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUService",
        plural="dpuservices",
        namespaced=True,
        display_name="DPU Service",
        description="Deploy a Helm chart to DPU nodes (via ArgoCD)",
        category=ResourceCategory.DPF
    ),
    "dpudeployment": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUDeployment",
        plural="dpudeployments",
        namespaced=True,
        display_name="DPU Deployment",
        description="Group of DPUServices + DPUSets for coordinated deployment",
        category=ResourceCategory.DPF
    ),
    "dpuservicechain": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUServiceChain",
        plural="dpuservicechains",
        namespaced=True,
        display_name="DPU Service Chain",
        description="OVS flows for traffic steering between service functions",
        category=ResourceCategory.DPF
    ),
    "dpuserviceinterface": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUServiceInterface",
        plural="dpuserviceinterfaces",
        namespaced=True,
        display_name="DPU Service Interface",
        description="OVS ports for service function chains",
        category=ResourceCategory.DPF
    ),
    "dpuserviceipam": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUServiceIPAM",
        plural="dpuserviceipams",
        namespaced=True,
        display_name="DPU Service IPAM",
        description="IP address management for DPU service chains",
        category=ResourceCategory.DPF
    ),
    "dpuservicecredreq": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="DPUServiceCredentialRequest",
        plural="dpuservicecredentialrequests",
        namespaced=True,
        display_name="DPU Service Credential",
        description="Cross-cluster auth (host ↔ DPU cluster)",
        category=ResourceCategory.DPF
    ),
    "servicechain": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="ServiceChain",
        plural="servicechains",
        namespaced=True,
        display_name="Service Chain",
        description="Service chain definition for DPU traffic steering",
        category=ResourceCategory.DPF
    ),
    "serviceinterface": K8sResourceType(
        api_group=ApiGroups.DPF_SERVICE,
        api_version="v1alpha1",
        kind="ServiceInterface",
        plural="serviceinterfaces",
        namespaced=True,
        display_name="Service Interface",
        description="Service interface for DPU service function chains",
        category=ResourceCategory.DPF
    ),

    # =========================================================================
    # NVIDIA DPF RESOURCES — Operator (operator.dpu.nvidia.com)
    # =========================================================================
    "dpfoperatorconfig": K8sResourceType(
        api_group=ApiGroups.DPF_OPERATOR,
        api_version="v1alpha1",
        kind="DPFOperatorConfig",
        plural="dpfoperatorconfigs",
        namespaced=True,
        display_name="DPF Operator Config",
        description="Global DPF operator configuration (system components, networking)",
        category=ResourceCategory.DPF
    ),

    # =========================================================================
    # F5 CIS RESOURCES (cis.f5.com) — D-023 Phase 1
    # Display overlay only (D-019): unknown CIS kinds still surface as
    # "discovered" — these entries just add friendly names and a category.
    # =========================================================================
    "cis_virtualserver": K8sResourceType(
        api_group=ApiGroups.F5_CIS,
        api_version="v1",
        kind="VirtualServer",
        plural="virtualservers",
        namespaced=True,
        display_name="CIS Virtual Server",
        description="F5 CIS VirtualServer — L7 virtual server for BIG-IP (EOL Apr 2026)",
        category=ResourceCategory.F5_CIS,
    ),
    "cis_transportserver": K8sResourceType(
        api_group=ApiGroups.F5_CIS,
        api_version="v1",
        kind="TransportServer",
        plural="transportservers",
        namespaced=True,
        display_name="CIS Transport Server",
        description="F5 CIS TransportServer — L4 TCP/UDP virtual server for BIG-IP (EOL Apr 2026)",
        category=ResourceCategory.F5_CIS,
    ),
    "cis_ingresslink": K8sResourceType(
        api_group=ApiGroups.F5_CIS,
        api_version="v1",
        kind="IngressLink",
        plural="ingresslinks",
        namespaced=True,
        display_name="CIS IngressLink",
        description="F5 CIS IngressLink — NGINX Ingress + BIG-IP integration (EOL Apr 2026)",
        category=ResourceCategory.F5_CIS,
    ),
    "cis_tlsprofile": K8sResourceType(
        api_group=ApiGroups.F5_CIS,
        api_version="v1",
        kind="TLSProfile",
        plural="tlsprofiles",
        namespaced=True,
        display_name="CIS TLS Profile",
        description="F5 CIS TLSProfile — TLS termination configuration for BIG-IP (EOL Apr 2026)",
        category=ResourceCategory.F5_CIS,
    ),
    "cis_policy": K8sResourceType(
        api_group=ApiGroups.F5_CIS,
        api_version="v1",
        kind="Policy",
        plural="policies",
        namespaced=True,
        display_name="CIS Policy",
        description="F5 CIS Policy — reusable traffic policy for VirtualServer/TransportServer (EOL Apr 2026)",
        category=ResourceCategory.F5_CIS,
    ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_resource_type(key: str) -> K8sResourceType:
    """Get a resource type by key."""
    resource_type = RESOURCE_REGISTRY.get(key)
    if not resource_type:
        raise ValueError(f"Unknown resource type: {key}")
    return resource_type


def list_resource_types() -> dict[str, K8sResourceType]:
    """Get all registered resource types."""
    return RESOURCE_REGISTRY.copy()


def list_namespaced_resources() -> dict[str, K8sResourceType]:
    """Get all namespaced resource types."""
    return {k: v for k, v in RESOURCE_REGISTRY.items() if v.namespaced}


def list_cluster_resources() -> dict[str, K8sResourceType]:
    """Get all cluster-scoped (non-namespaced) resource types."""
    return {k: v for k, v in RESOURCE_REGISTRY.items() if not v.namespaced}


def list_resources_by_category(category: str) -> dict[str, K8sResourceType]:
    """Get all resource types for a specific category."""
    return {k: v for k, v in RESOURCE_REGISTRY.items() if v.category == category}


def list_categories() -> list:
    """Get all unique categories."""
    return list(set(v.category for v in RESOURCE_REGISTRY.values()))

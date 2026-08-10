#!/bin/bash
# Build the BNK Operator Docker image
#
# Usage:
#   ./build.sh                          # Build locally as bnk-operator:1.0.0
#   ./build.sh --push ecr               # Build and push to ECR
#   ./build.sh --push dockerhub         # Build and push to Docker Hub
#   ./build.sh --tag 1.1.0              # Custom tag
#
# Prerequisites:
#   - Docker (or colima/podman)
#   - For ECR: aws CLI configured, ECR repo created
#   - For Docker Hub: docker login done

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Defaults
IMAGE_NAME="bnk-operator"
TAG="${TAG:-1.1.0}"
PUSH_TARGET=""
PLATFORM="${PLATFORM:-linux/amd64}"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --push)  PUSH_TARGET="$2"; shift 2 ;;
        --tag)   TAG="$2"; shift 2 ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        *)       echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "============================================"
echo "  BNK Operator Image Build"
echo "============================================"
echo "  Image:     ${IMAGE_NAME}:${TAG}"
echo "  Platform:  ${PLATFORM}"
echo "  Push:      ${PUSH_TARGET:-local only}"
echo "============================================"

# Build
echo ""
echo "Building..."
docker build \
    --platform "${PLATFORM}" \
    -t "${IMAGE_NAME}:${TAG}" \
    -t "${IMAGE_NAME}:latest" \
    .

echo "Build complete: ${IMAGE_NAME}:${TAG}"
docker images "${IMAGE_NAME}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

# Push if requested
if [[ -n "$PUSH_TARGET" ]]; then
    case "$PUSH_TARGET" in
        ecr)
            AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
            AWS_REGION=${AWS_REGION:-us-east-1}
            ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${IMAGE_NAME}"

            echo ""
            echo "Pushing to ECR: ${ECR_REPO}:${TAG}"

            aws ecr get-login-password --region "${AWS_REGION}" | \
                docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

            # Create repo if it doesn't exist
            aws ecr describe-repositories --repository-names "${IMAGE_NAME}" --region "${AWS_REGION}" 2>/dev/null || \
                aws ecr create-repository --repository-name "${IMAGE_NAME}" --region "${AWS_REGION}"

            docker tag "${IMAGE_NAME}:${TAG}" "${ECR_REPO}:${TAG}"
            docker tag "${IMAGE_NAME}:${TAG}" "${ECR_REPO}:latest"
            docker push "${ECR_REPO}:${TAG}"
            docker push "${ECR_REPO}:latest"

            echo "Pushed to: ${ECR_REPO}:${TAG}"
            ;;

        dockerhub|docker)
            DOCKER_USER=${DOCKER_USER:-f5devops}
            DOCKER_REPO="${DOCKER_USER}/${IMAGE_NAME}"

            echo ""
            echo "Pushing to Docker Hub: ${DOCKER_REPO}:${TAG}"

            docker tag "${IMAGE_NAME}:${TAG}" "${DOCKER_REPO}:${TAG}"
            docker tag "${IMAGE_NAME}:${TAG}" "${DOCKER_REPO}:latest"
            docker push "${DOCKER_REPO}:${TAG}"
            docker push "${DOCKER_REPO}:latest"

            echo "Pushed to: ${DOCKER_REPO}:${TAG}"
            ;;

        local|kind)
            echo ""
            echo "Loading into kind cluster..."
            kind load docker-image "${IMAGE_NAME}:${TAG}" 2>/dev/null || \
                echo "Note: kind not found or no cluster running. Image is available locally."
            ;;

        *)
            # Treat as a full registry URL
            FULL_IMAGE="${PUSH_TARGET}/${IMAGE_NAME}:${TAG}"
            echo "Pushing to: ${FULL_IMAGE}"
            docker tag "${IMAGE_NAME}:${TAG}" "${FULL_IMAGE}"
            docker push "${FULL_IMAGE}"
            ;;
    esac
fi

echo ""
echo "Done! To deploy:"
echo ""
echo "  helm install bnk-operator ./charts/bnk-operator \\"
echo "    --namespace bnk-operator --create-namespace \\"
echo "    --set controlPlane.url=wss://<BNK_FORGE_HOST>/ws/operator \\"
echo "    --set controlPlane.token=<TOKEN> \\"
echo "    --set image.repository=${IMAGE_NAME} \\"
echo "    --set image.tag=${TAG}"
echo ""

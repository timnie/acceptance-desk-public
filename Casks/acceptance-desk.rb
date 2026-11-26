cask "acceptance-desk" do
  version "10"
  sha256 "051cf79ec00860337939837dce2d6599c3cee451377f2ce4fa66b91ce8b691c7"

  url "https://github.com/timnie/acceptance-desk-public/releases/download/macos-beta-build-#{version}-zip/Acceptance-Desk-macOS-arm64-Build-#{version}.zip"
  name "Acceptance Desk"
  desc "Review AI edits to Chinese manuscripts"
  homepage "https://github.com/timnie/acceptance-desk-public"

  depends_on arch: :arm64

  app "AI 改稿验收台.app"
end

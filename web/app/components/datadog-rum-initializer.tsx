'use client'

import { datadogRum } from '@datadog/browser-rum'
import { useEffect } from 'react'

const DatadogRumInitializer = ({
  children,
}: { children: React.ReactElement }) => {
  useEffect(() => {
    console.log('[DatadogRUM] Initializing...')
    try {
      datadogRum.init({
        applicationId: '80992597-6ca6-4d3e-9daf-d358d4aa0408',
        clientToken: 'pub2bc1d648e0990849dd76fc42796684fa',
        site: 'datadoghq.com',
        service: 'dify-web',
        env: 'local',
        sessionSampleRate: 100,
        sessionReplaySampleRate: 100,
        trackResources: true,
        trackLongTasks: true,
        trackUserInteractions: true,
        defaultPrivacyLevel: 'allow',
      })
      console.log('[DatadogRUM] Init SUCCESS - applicationId: 80992597-..., site: datadoghq.com')
    }
    catch (e) {
      console.error('[DatadogRUM] Init FAILED:', e)
    }
  }, [])
  return children
}

export default DatadogRumInitializer

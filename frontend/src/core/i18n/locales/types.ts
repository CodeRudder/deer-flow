import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
    loadMore: string;
    retry: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    regenerate: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Artifact viewer (mobile full-screen page, prototype ⑤)
  artifactViewer: {
    /** Header subtitle, e.g. "3 artifacts". */
    count: (count: number) => string;
    /** Code view toggle: long lines wrap onto the next line. */
    wrapLines: string;
    /** Code view toggle: long lines scroll horizontally instead. */
    noWrapLines: string;
    /** Fallback view for a file the browser cannot display. */
    downloadOnly: string;
    /** Shown when the thread or the artifact could not be fetched. */
    loadFailed: string;
    /** Screen-reader label of the header back button. */
    back: string;
    /**
     * Accessible name of the header's file switcher, which is the filename
     * itself followed by a chevron; the value says what tapping it does.
     */
    switchFile: (current: string) => string;
    /** Placeholder of the file picker's search box. */
    searchFiles: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    imageGeneration: string;
    imageGenerationNotConfigured: string;
    imageGenerationSkillDisabled: string;
    imageGenerationLoadFailed: string;
    videoGeneration: string;
    videoGenerationNotConfigured: string;
    videoGenerationSkillDisabled: string;
    videoGenerationLoadFailed: string;
    videoPointsPerSecond: string;
    videoRateFromLabel: (rate: string) => string;
    videoRegenerationLabel: string;
    videoDurationRange: string;
    videoDurationSeconds: string;
    // Label of the mobile `＋` panel's model row. The row opens the same
    // chat/vision picker as the desktop, so the two labels below are the
    // category toggle *inside* it, not this one.
    model: string;
    chatModel: string;
    visionModel: string;
    defaultVisionModel: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestionPlaceholderRequired: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
    // Mobile composer (prototype ②③): the main row keeps only ＋ / input /
    // send, so this is where the labels for the controls that moved into the
    // ＋ panel, plus the two submit-button states, live.
    composerOptions: string;
    /**
     * Accessible name of the composer's persistent mode pill, carrying the
     * current value because the pill itself only shows the short label.
     */
    switchMode: (current: string) => string;
    /** Accessible name of the composer's persistent model pill. */
    switchModel: (current: string) => string;
    photoLibrary: string;
    takePhoto: string;
    file: string;
    sendMessage: string;
    stopGenerating: string;
    planMode: string;
    planModeOn: string;
    planModeOff: string;
    skillCommands: string;
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    agents: string;
    channels: string;
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepCheckErrorWithDetail: string;
    nameStepApiDisabledError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    edit: string;
    editTitle: string;
    editDescription: string;
    descriptionLabel: string;
    soulLabel: string;
    modelLabel: string;
    modelDefault: string;
    capabilitiesLabel: string;
    skillsLabel: string;
    toolGroupsLabel: string;
    skillsInheritAll: string;
    skillsNone: string;
    toolGroupsInheritAll: string;
    toolGroupsNone: string;
    dirtyConfirmTitle: string;
    dirtyConfirmBody: string;
    dirtyConfirmDiscard: string;
    dirtyConfirmKeep: string;
    saveSuccess: string;
    apiDisabledError: string;
    legacyOnlyError: string;
    loadDetailFailed: string;
    createChoiceDescription: string;
    createChoiceManualTitle: string;
    createChoiceManualDescription: string;
    createChoiceChatTitle: string;
    createChoiceChatDescription: string;
    createTitle: string;
    createDescription: string;
    nameLabel: string;
    createSoulPlaceholder: string;
    createSuccess: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    logout: string;
    gatewayUnavailable: string;
    gatewayUnavailableRetrying: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
  };

  // Chats
  chats: {
    searchChats: string;
    loadMoreToSearch: string;
    loadingMore: string;
    loadOlderChats: string;
    // Mobile thread list (prototype ①): time-group headers and row actions.
    pinned: string;
    today: string;
    yesterday: string;
    earlier: string;
    empty: string;
    noSearchResults: string;
    /**
     * Deliberately distinct from `empty`: the list must not tell the user they
     * have no conversations when the request that fetches them failed. The
     * mobile thread list renders this — with a retry — once the query errors.
     */
    loadFailed: string;
    pin: string;
    unpin: string;
    actions: string;
    deleteConfirmTitle: string;
    deleteConfirm: string;
    // Mobile chat screen (prototype ②): the header's ⋯ menu and the floating
    // "back to bottom" affordance. The desktop header hardcodes the session
    // status title, so it is spelled out here for both trees.
    sessionStatus: string;
    scrollToBottom: string;
  };

  // Channels
  channels: {
    title: string;
    connect: string;
    modify: string;
    reconnect: string;
    disconnect: string;
    connected: string;
    notConnected: string;
    pending: string;
    revoked: string;
    disabled: string;
    unconfigured: string;
    unavailable: string;
    unavailableShort: string;
    setupTitle: (name: string) => string;
    setupEditTitle: (name: string) => string;
    setupDescription: string;
    saveAndConnect: string;
    saveChanges: string;
    descriptions: Record<string, string>;
    connectedAs: (name: string) => string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executedSteps: (count: number) => string;
    toolsUsed: (count: number) => string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
  };

  // Human Input (clarification cards)
  humanInput: {
    answered: string;
    pending: string;
    readOnly: string;
    otherLabel: string;
    otherPlaceholder: string;
    submit: string;
    emptyError: string;
    requiredError: string;
    requiredA11yLabel: string;
    selectPlaceholder: string;
    inputDisabledHint: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
  };

  // Quota indicator (feat-df-8)
  quotaIndicator: {
    label: string;
    prefixLabel: string;
    reserved: string;
    unitPoints: string;
    unitCount: string;
    periodWeekly: string;
    periodMonthly: string;
    overrideNote: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      channels: string;
      memory: string;
      models: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    models: {
      title: string;
      description: string;
      adminRequired: string;
      loadError: string;
      empty: string;
      add: string;
      addTitle: string;
      edit: string;
      editTitle: string;
      delete: string;
      deleteConfirmTitle: string;
      deleteConfirm: string;
      test: string;
      testOk: string;
      testFailed: string;
      saving: string;
      save: string;
      cancel: string;
      saveSuccess: string;
      deleteSuccess: string;
      name: string;
      namePlaceholder: string;
      provider: string;
      providerPlaceholder: string;
      providerUnavailable: string;
      providerUnavailableHint: string;
      apiBase: string;
      apiBasePlaceholder: string;
      apiBaseOptionalHint: string;
      apiBaseRequiredHint: string;
      apiBaseRequired: string;
      model: string;
      modelPlaceholder: string;
      displayName: string;
      displayNamePlaceholder: string;
      apiKey: string;
      apiKeyPlaceholder: string;
      apiKeyKeepHint: string;
      apiKeyStoredAsHint: string;
      keyNotSet: string;
      required: string;
      requiredForProvider: string;
      editTitleProviderFallback: string;
      supportsThinking: string;
      supportsThinkingHint: string;
      probeThinking: string;
      thinkingUnavailable: string;
      budgetTokens: string;
      budgetHint: string;
      maxTokens: string;
      maxTokensHint: string;
      thinkNotProbed: string;
      thinkProbing: string;
      thinkProbeFailed: string;
      thinkEnabled: string;
      thinkNotEnabled: string;
      thinkDisables: string;
      thinkIgnoresDisable: string;
      thinkAlwaysOn: string;
    };
    tools: {
      title: string;
      description: string;
      adminRequired: string;
      empty: string;
    };
    channels: {
      title: string;
      description: string;
      disabled: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      ssoProvider: string;
      ssoPasswordDescription: string;
      ssoPasswordMessage: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };

  // Login / Auth
  login: {
    signInTitle: string;
    createAccountTitle: string;
    email: string;
    emailPlaceholder: string;
    password: string;
    passwordPlaceholder: string;
    pleaseWait: string;
    signIn: string;
    createAccount: string;
    registrationPendingTitle: string;
    registrationPendingDescription: string;
    registrationPendingBackToLogin: string;
    createAdminAccount: string;
    adminSetupRequiredTitle: string;
    adminSetupRequiredDescription: string;
    orContinueWith: string;
    ssoHint: string;
    continueWith: (provider: string) => string;
    noAccountSignUp: string;
    haveAccountSignIn: string;
    backToHome: string;
    officialWebsite: string;
    networkError: string;
    authFailed: string;
    errors: {
      sso_failed: string;
      sso_cancelled: string;
      sso_account_exists: string;
      sso_not_allowed: string;
      registration_pending: string;
      account_disabled: string;
    };
    // Password validation failures on the mobile setup screen. The desktop
    // setup page hardcodes these English strings; the mobile page routes them
    // through i18n like the rest of `login`.
    passwordsDoNotMatch: string;
    passwordTooShort: string;
    // Mobile SSO callback screen states.
    signingIn: string;
    redirecting: string;
    authFailedRedirecting: string;
    // Mobile setup screen: admin bootstrap and the forced password change that
    // follows a first login. The desktop page hardcodes its copy in English.
    setup: {
      createAdminTitle: string;
      createAdminDescription: string;
      passwordPlaceholder: string;
      confirmPassword: string;
      confirmPasswordPlaceholder: string;
      creatingAccount: string;
      completeSetupTitle: string;
      completeSetupDescription: string;
      emailPlaceholder: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      settingUp: string;
      completeSetupAction: string;
    };
  };
}

using System;
using System.IO;
using UnityEngine;
using UnityEngine.UIElements;

// Isolated UI Toolkit fixture. No game code, packages, saves or network access.
public class ProbeUi : MonoBehaviour
{
    [Serializable] public class State
    {
        public int returns, escapes;
        public bool overlay = true;
        public bool ready;
        public string text = "", graphics, unity;
        public float x, y, width, height;
    }
    public static State state = new State();
    static string Output => Path.Combine(Application.dataPath, "../probe-state.json");
    TextField field;
    VisualElement overlay;
    Label status;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void StartFixture()
    {
        var go = new GameObject("WinAuto UI Toolkit Probe");
        go.AddComponent<ProbeUi>();
    }

    void Awake()
    {
        state = new State {graphics = SystemInfo.graphicsDeviceType.ToString(), unity = Application.unityVersion};
        Application.runInBackground = true;
        var camera = new GameObject("Probe Camera").AddComponent<Camera>();
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(24f/255f, 48f/255f, 80f/255f);
        var panel = ScriptableObject.CreateInstance<PanelSettings>();
        panel.scaleMode = PanelScaleMode.ConstantPixelSize;
        panel.themeStyleSheet = Resources.Load<ThemeStyleSheet>("ProbeTheme");
        var document = gameObject.AddComponent<UIDocument>();
        document.panelSettings = panel;
        var root = document.rootVisualElement;
        root.style.flexGrow = 1;
        root.style.backgroundColor = (Color)new Color32(24, 48, 80, 255);
        root.style.paddingLeft = 40;
        root.style.paddingTop = 60;
        root.Add(new Label("WinAuto UI Toolkit Probe"));
        field = new TextField("Command") { name = "command" };
        field.style.width = 500;
        field.style.height = 50;
        root.Add(field);
        status = new Label("Return: 0 / Escape: 0");
        status.style.color = Color.white;
        root.Add(status);
        overlay = new VisualElement();
        overlay.style.width = 500;
        overlay.style.height = 150;
        overlay.style.backgroundColor = (Color)new Color32(180, 26, 64, 255);
        overlay.Add(new Label("Escape dismisses this overlay"));
        root.Add(overlay);
        field.RegisterCallback<KeyDownEvent>(evt => {
            if (evt.keyCode == KeyCode.Return || evt.keyCode == KeyCode.KeypadEnter)
            {
                state.returns++;
                state.text = field.value;
                UpdateState();
            }
        }, TrickleDown.TrickleDown);
        root.RegisterCallback<KeyDownEvent>(evt => {
            if (evt.keyCode == KeyCode.Escape)
            {
                state.escapes++;
                state.overlay = false;
                overlay.style.display = DisplayStyle.None;
                UpdateState();
            }
        }, TrickleDown.TrickleDown);
        field.RegisterCallback<GeometryChangedEvent>(evt => {
            var bounds = field.worldBound;
            state.x = bounds.x; state.y = bounds.y;
            state.width = bounds.width; state.height = bounds.height;
            UpdateState();
        });
    }

    void UpdateState()
    {
        status.text = $"Return: {state.returns} / Escape: {state.escapes}";
        File.WriteAllText(Output, JsonUtility.ToJson(state));
    }

    void Update()
    {
        if (!state.ready && Time.frameCount >= 30 && UnityEngine.Rendering.SplashScreen.isFinished)
        {
            state.ready = true;
            UpdateState();
        }
    }
}

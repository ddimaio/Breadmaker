# Bread Maker Companion

Streamlit companion app for the Gastroback »Design Bread Maker Advanced« (Art.-No. 62823), built from the operating manual.

- Pick any of the manual's recipes (bread, cake, dough, yoghurt, jam, ice cream) or your own
- Choose loaf size (1000 / 750 / 500 g) and crust colour
- Step-by-step checklist with amounts in g/ml and cups/tbsp/tsp
- Program timer tracking the machine phase, with alerts for paddle removal, timed add-ins and completion (in-app beep, browser notification, optional ntfy.sh phone push)
- Custom recipe editor with JSON import/export

## Run

```
pip install -r requirements.txt
streamlit run breadmaker_app.py
```

Data (custom recipes, settings, phase calibration, active timer) is stored in `breadmaker_data/` next to the script.

Phase times are only published for program 17 (Homemade); for other programs the app spreads the LCD total time over the standard phase sequence. Use Sync on the Timer page or calibrate phases in Settings.

## Using it on your phone

The layout adapts to phone screens: navigation sits at the top, ingredients show as cards (metric + cups/spoons), and the timer page shows one large countdown with full-width Pause/Stop buttons. The custom recipe editor is easiest on a larger screen.

**Alerts on the phone**

- *ntfy push (most reliable):* install the ntfy app, subscribe to a hard-to-guess topic and enter it in Settings. The server sends the pushes, so they arrive even with the screen off or the browser closed.
- *Browser notifications:* only work on `localhost` or `https`. Serve the app over https on your tailnet with

  ```
  streamlit run breadmaker_app.py            # on the PC, port 8501
  tailscale serve --bg 8501                  # exposes https://<machine>.<tailnet>.ts.net
  ```

  then open the `https://…ts.net` address on the phone and tap "Enable browser notifications" in Settings. On iPhone the page must also be added to the Home Screen (Share → Add to Home Screen) and opened from there.
- Phones often block sound that plays without a tap, and a locked screen pauses the page, so in-app alerts appear when you next look. The timer itself is server-side and is always correct when you return.

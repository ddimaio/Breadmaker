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

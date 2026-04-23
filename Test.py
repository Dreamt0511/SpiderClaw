from textual.app import App
from textual.widgets import Header, Footer, Log

class MyApp(App):
    def compose(self):
        yield Header()
        yield Log()
        yield Footer()

if __name__ == "__main__":
    MyApp().run()
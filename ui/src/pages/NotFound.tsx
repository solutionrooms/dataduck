import { Link } from "react-router";
import { EmptyState } from "../components/States";

export function NotFoundPage() {
  return (
    <div className="panel">
      <EmptyState
        title="Page not found"
        description="This page does not exist in Duckbricks."
        action={
          <Link to="/" className="btn">
            Go home
          </Link>
        }
      />
    </div>
  );
}
